"""Only session-local temporary tables; no catalog or financial fixture mutations."""
from contextlib import contextmanager

import pytest
import psycopg
from fastapi.testclient import TestClient

from app.db import get_connection
from app.main import app
from app.routers import experiences
from scripts import apply_experience_products as migration


@pytest.fixture
def case(monkeypatch):
    conn = get_connection()
    conn.autocommit = True
    try:
        for table in ('destinations', 'products', 'partners', 'product_partners', 'schema_migrations'):
            conn.execute(f'CREATE TEMP TABLE {table} (LIKE public.{table} INCLUDING ALL)')
        conn.execute('SET search_path=pg_temp,public')
        for name, checksum in migration.checksums().items():
            if name != migration.NAME:
                conn.execute('INSERT INTO schema_migrations(version,checksum) VALUES (%s,%s)', (name,checksum))
        with conn.transaction():
            migration.apply(conn)
        assert conn.execute("SELECT relpersistence FROM pg_class WHERE oid='experience_products'::regclass").fetchone()==('t',)
        dest=conn.execute("INSERT INTO destinations(code,name,slug) VALUES ('PARACAS','Fictional','test') RETURNING id").fetchone()[0]
        product=conn.execute("INSERT INTO products(destination_id,code,name,slug,product_type,status,reservations_enabled) VALUES (%s,'TEST-PRODUCT','Fictional option','test','tour','active',true) RETURNING id",(dest,)).fetchone()[0]
        partner=conn.execute("INSERT INTO partners(code,business_name,status,reservations_enabled) VALUES ('TEST-PARTNER','Private name','active',true) RETURNING id").fetchone()[0]
        conn.execute('INSERT INTO product_partners(product_id,partner_id) VALUES (%s,%s)',(product,partner))
        conn.execute("INSERT INTO experience_products(destination_id,experience_key,product_id,status) VALUES (%s,'ballestas',%s,'active')",(dest,product))
        @contextmanager
        def connection():
            with conn.transaction():
                yield conn
        monkeypatch.setattr(experiences,'get_connection',connection)
        # No lifespan/login: the endpoint is public and performs no auth writes.
        with TestClient(app, raise_server_exceptions=True) as client:
            yield conn,client,dest,product,partner
    finally:
        conn.close()


def read(case, experience='ballestas', destination='PARACAS'):
    return case[1].get(f'/experiences/{destination}/{experience}/commercial-options')


def test_public_eligible_and_no_private_data(case):
    response=read(case)
    assert response.status_code==200
    option=response.json()['options'][0]
    assert set(option)=={'product_id','code','name','requestable','reason'}
    assert option['requestable'] is True and option['reason'] is None
    assert 'Private name' not in response.text and str(case[4]) not in response.text


@pytest.mark.parametrize('experience',['unknown','beaches'])
def test_no_published_association(case,experience):
    assert read(case,experience).json()==dict(experience=experience,destination='PARACAS',options=[])


def test_missing_destination(case):
    assert read(case,destination='UNKNOWN').status_code==404


@pytest.mark.parametrize('table,status',[('experience_products','draft'),('experience_products','inactive'),('products','draft'),('products','paused'),('products','inactive')])
def test_unpublished(case,table,status):
    case[0].execute(f'UPDATE {table} SET status=%s',(status,))
    assert read(case).json()['options']==[]


@pytest.mark.parametrize('table,field,value',[
    ('partners','status','pending'),('partners','status','inactive'),('partners','status','suspended'),
    ('partners','reservations_enabled',False),('product_partners','status','inactive'),('product_partners','status','paused')])
def test_ineligible_provider(case,table,field,value):
    case[0].execute(f'UPDATE {table} SET {field}=%s',(value,))
    option=read(case).json()['options'][0]
    assert option['requestable'] is False and option['reason']=='no_eligible_providers'


def test_no_providers(case):
    case[0].execute('DELETE FROM product_partners')
    assert read(case).json()['options'][0]['reason']=='no_eligible_providers'


def test_product_disabled(case):
    case[0].execute('UPDATE products SET reservations_enabled=false')
    assert read(case).json()['options'][0]['reason']=='commercial_option_unavailable'


@pytest.mark.parametrize('key',['Ballestas','bad key','-bad','bad--key','bad_'])
def test_invalid_key_http_and_sql(case,key):
    assert read(case,key).status_code==422
    with pytest.raises(psycopg.errors.CheckViolation):
        case[0].execute('UPDATE experience_products SET experience_key=%s',(key,))


def test_cross_destination_rejected(case):
    other=case[0].execute("INSERT INTO destinations(code,name,slug) VALUES ('OTHER','Fictional','other') RETURNING id").fetchone()[0]
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        case[0].execute('UPDATE experience_products SET destination_id=%s',(other,))


def test_multiple_and_dedup(case):
    conn,_,dest,product,_=case
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute("INSERT INTO experience_products(destination_id,experience_key,product_id) VALUES (%s,'ballestas',%s)",(dest,product))
    second=conn.execute("INSERT INTO products(destination_id,code,name,slug,product_type,status,reservations_enabled) VALUES (%s,'AAA','Another option','another','tour','active',true) RETURNING id",(dest,)).fetchone()[0]
    conn.execute("INSERT INTO experience_products(destination_id,experience_key,product_id,status) VALUES (%s,'ballestas',%s,'active')",(dest,second))
    assert [o['code'] for o in read(case).json()['options']]==['AAA','TEST-PRODUCT']


def test_migration_idempotent_and_checksum(case):
    with case[0].transaction(): migration.apply(case[0])
    ledger=migration.checksums()
    ledger[migration.NAME]='incorrect'
    with pytest.raises(RuntimeError,match='checksum'): migration.migration_sql(ledger)
    ledger.pop(migration.NAME);ledger['011_ingestion_apply.sql']='incorrect'
    with pytest.raises(RuntimeError,match='checksum'): migration.migration_sql(ledger)


def test_read_only_transaction(case,monkeypatch):
    original=experiences.OPTIONS_SQL
    monkeypatch.setattr(experiences, 'OPTIONS_SQL', original.replace(
        "AND product.status='active'", "AND product.status='active' AND current_setting('transaction_read_only')='on'"))
    assert len(read(case).json()['options'])==1


def test_empty_infrastructure(case):
    case[0].execute('DELETE FROM experience_products')
    assert read(case).json()==dict(experience='ballestas',destination='PARACAS',options=[])


def test_official_demo_upgrade_route(monkeypatch):
    import json
    from scripts import setup_demo
    ledger={k:v for k,v in migration.checksums().items() if k!=migration.NAME}
    calls=[]
    monkeypatch.setattr(setup_demo,'check_marker',lambda:None)
    monkeypatch.setattr(setup_demo,'sql',lambda *args:json.dumps(ledger))
    monkeypatch.setattr(migration,'run_demo',lambda persist=False:calls.append(persist))
    setup_demo.upgrade_demo_schema(migration.checksums())
    assert calls==[True]


def test_delete_restricted(case):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        case[0].execute('DELETE FROM products WHERE id=%s',(case[3],))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        case[0].execute('DELETE FROM destinations WHERE id=%s',(case[2],))


def test_invalid_destination(case):
    assert read(case,destination='paracas').status_code==422
