"""013 only on disposable DBs; genuine concurrent transactions, synthetic identities."""
import os
from contextlib import contextmanager
from datetime import date,timedelta
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import threading
from pathlib import Path

import pytest
import psycopg
from psycopg import sql
from fastapi.testclient import TestClient
from app.db import get_connection as connect
from app.main import app
from app import auth
from app.routers import partner_responses as pr, reservations as res, service_requests as sr
from scripts import apply_offer_consent as migration


@pytest.fixture
def case(monkeypatch):
    if not os.getenv('DB_NAME','').startswith('h4u_ingestion_test_'):
        pytest.fail('Use isolated test runner',pytrace=False)
    schema='consent_'+uuid4().hex
    owner=connect();owner.autocommit=True
    owner.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    try:
        for (table,) in owner.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall():
            if table=='request_offer_consents': continue
            owner.execute(sql.SQL('CREATE TABLE {}.{} (LIKE public.{} INCLUDING ALL)').format(*map(sql.Identifier,(schema,table,table))))
        owner.execute(sql.SQL('SET search_path TO {},public').format(sql.Identifier(schema)))
        # A schema-only clone may already contain 013 columns from persistent demo.
        # Remove only these columns in this newly created, empty test schema.
        for table,columns in {'service_requests':['terms_revision'],'request_partners':['offer_version','offer_request_revision','offer_request_snapshot'],'reservations':['consent_id']}.items():
            for column in columns:
                owner.execute(sql.SQL('ALTER TABLE {} DROP COLUMN IF EXISTS {} CASCADE').format(sql.Identifier(table),sql.Identifier(column)))
        # Isolated runner baselines the registry at 010; use file checksums only here.
        for name,digest in migration.checksums().items():
            if name!=migration.NAME: owner.execute('INSERT INTO schema_migrations(version,checksum) VALUES (%s,%s)',(name,digest))
        # Historical rows exist before 013; they must not gain fabricated consent.
        historical=owner.execute("INSERT INTO reservations(code,service_request_id,product_id,partner_id,service_date,passenger_count,agreed_price,status) VALUES ('HISTORICAL',%s,%s,%s,current_date,1,10,'completed') RETURNING id",(uuid4(),uuid4(),uuid4())).fetchone()[0]
        before=owner.execute('SELECT to_jsonb(r) FROM reservations r WHERE id=%s',(historical,)).fetchone()[0]
        with owner.transaction(force_rollback=True): migration.apply(owner)
        assert owner.execute("SELECT to_regclass(%s)",(schema+'.request_offer_consents',)).fetchone()==(None,)
        with owner.transaction(): migration.apply(owner)
        with owner.transaction(): migration.apply(owner)
        after=owner.execute("SELECT to_jsonb(r)-'consent_id' FROM reservations r WHERE id=%s",(historical,)).fetchone()[0]
        assert before==after
        assert owner.execute('SELECT count(*) FROM request_offer_consents').fetchone()==(0,)
        @contextmanager
        def connection():
            with connect() as conn:
                conn.execute(sql.SQL('SET search_path TO {},public').format(sql.Identifier(schema)))
                yield conn
        for module in (auth,pr,res,sr):monkeypatch.setattr(module,'get_connection',connection)
        d=owner.execute("INSERT INTO destinations(code,name,slug) VALUES ('TEST','Fictional','test') RETURNING id").fetchone()[0]
        traveler=owner.execute('INSERT INTO travelers DEFAULT VALUES RETURNING id').fetchone()[0]
        user=owner.execute("INSERT INTO users(email,password_hash,role,traveler_id) VALUES ('test@example.invalid','not-a-login-credential','tourist',%s) RETURNING id",(traveler,)).fetchone()[0]
        session=owner.execute("INSERT INTO sessions(traveler_id,destination_id,channel) VALUES (%s,%s,'test') RETURNING id",(traveler,d)).fetchone()[0]
        product=owner.execute("INSERT INTO products(destination_id,code,name,slug,product_type,status,reservations_enabled) VALUES (%s,'TEST','Fictional','test','tour','active',true) RETURNING id",(d,)).fetchone()[0]
        partners=[]
        for code in ('A','B'):
            p=owner.execute("INSERT INTO partners(code,business_name,status,reservations_enabled) VALUES (%s,'Fictional','active',true) RETURNING id",(code,)).fetchone()[0];partners.append(p)
            owner.execute('INSERT INTO product_partners(product_id,partner_id,partner_price) VALUES (%s,%s,100)',(product,p))
        request=sr.create_service_request(sr.ServiceRequestCreate(session_id=session,product_id=product,service_date=date.today()+timedelta(days=5),passenger_count=2,adults_count=2,minors_count=0))
        actor=auth.Principal(subject=str(user),role='tourist',traveler_id=traveler)
        previous=app.dependency_overrides.copy();app.dependency_overrides[auth.get_current_actor]=lambda:actor
        yield dict(db=owner,connection=connection,request=request,actor=actor,candidates=[x['request_partner_id'] for x in request['candidates']],client=TestClient(app),product=product)
        app.dependency_overrides.clear();app.dependency_overrides.update(previous)
    finally:
        owner.execute('SET search_path TO public')
        owner.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
        owner.close()


def offer(c,index=0,price='90'):
    candidate=c['candidates'][index]
    pr.respond_to_request(pr.PartnerResponseCreate(request_partner_id=candidate,action='counter_offer',proposed_price=price,proposed_currency='PEN',proposed_time='11:00',partner_message='Synthetic conditions'))
    return c['db'].execute('SELECT offer_version FROM request_partners WHERE id=%s',(candidate,)).fetchone()[0]


def accept(c,version,index=0):
    return c['client'].post('/partner-responses/'+c['candidates'][index]+'/accept-counter-offer',json={'expected_version':version})


@pytest.mark.parametrize('counter',[False,True])
def test_partner_accept_forbidden(case,counter):
    if counter: offer(case)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        pr.respond_to_request(pr.PartnerResponseCreate(request_partner_id=case['candidates'][0],action='accept',proposed_price=99,proposed_currency='USD'))
    assert e.value.status_code==409
    assert case['db'].execute('SELECT count(*) FROM request_partners WHERE is_winner').fetchone()==(0,)


def test_exact_consent_snapshot_and_retry(case):
    v=offer(case)
    assert case['db'].execute('SELECT count(*) FROM request_partners WHERE is_winner').fetchone()==(0,)
    a=accept(case,v);assert a.status_code==200,a.text
    b=accept(case,v);assert b.status_code==200 and b.json()['replayed'] is True
    assert a.json()['consent_id']==b.json()['consent_id']
    case['db'].execute('UPDATE product_partners SET partner_price=999')
    r=res.create_reservation(res.ReservationCreate(service_request_code=case['request']['code']))
    assert r['agreed_price']==90 and r['currency']=='PEN' and str(r['service_time'])=='11:00:00'
    assert case['db'].execute('SELECT count(*) FROM request_offer_consents').fetchone()==(1,)


@pytest.mark.parametrize('mutation',["UPDATE service_requests SET passenger_count=3,adults_count=3", "UPDATE service_requests SET preferred_time='12:00'", "UPDATE service_requests SET additional_notes='changed'"])
def test_request_changes_invalidate(case,mutation):
    v=offer(case);case['db'].execute(mutation)
    assert accept(case,v).status_code==409


def test_obsolete_offer(case):
    old=offer(case);new=offer(case,price='95');assert new>old
    assert accept(case,old).status_code==409
    assert accept(case,new).status_code==200


def test_ownership_and_roles(case):
    v=offer(case)
    app.dependency_overrides[auth.get_current_actor]=lambda:auth.Principal(subject=str(uuid4()),role='tourist',traveler_id=uuid4())
    assert accept(case,v).status_code==403
    app.dependency_overrides[auth.get_current_actor]=lambda:auth.Principal(subject=str(uuid4()),role='admin')
    assert accept(case,v).status_code==403


def test_expiration(case):
    v=offer(case);case['db'].execute("UPDATE request_partners SET expires_at=now()-interval '1 minute'")
    assert accept(case,v).status_code==409


@pytest.mark.parametrize('statement',["UPDATE request_offer_consents SET amount_total=1",'DELETE FROM request_offer_consents','TRUNCATE request_offer_consents CASCADE'])
def test_immutable(case,statement):
    assert accept(case,offer(case)).status_code==200
    with pytest.raises(psycopg.errors.CheckViolation):case['db'].execute(statement)


def test_atomic_rollback(case):
    v=offer(case)
    case['db'].execute("ALTER TABLE request_partners ADD CONSTRAINT test_failure CHECK (NOT is_winner) NOT VALID")
    assert accept(case,v).status_code==500
    assert case['db'].execute('SELECT count(*) FROM request_offer_consents').fetchone()==(0,)
    assert case['db'].execute('SELECT count(*) FROM service_requests WHERE assigned_partner_id IS NOT NULL').fetchone()==(0,)


def test_concurrent_one_winner(case):
    versions=[offer(case,0),offer(case,1)];barrier=threading.Barrier(2)
    def attempt(i):
        barrier.wait()
        return pr.accept_counter_offer(case['candidates'][i],pr.CounterOfferAcceptance(expected_version=versions[i]),case['actor'])
    def run(i):
        from fastapi import HTTPException
        try:return ('ok',attempt(i))
        except HTTPException as e:return ('rejected',e.status_code)
    with ThreadPoolExecutor(2) as pool: results=list(pool.map(run,(0,1)))
    assert sorted(x[0] for x in results)==['ok','rejected']
    assert case['db'].execute('SELECT count(*) FROM request_partners WHERE is_winner').fetchone()==(1,)
    assert case['db'].execute('SELECT count(*) FROM request_offer_consents').fetchone()==(1,)
    assert case['db'].execute("SELECT count(*) FROM request_partners WHERE status='lost'").fetchone()==(1,)


def test_second_partner_after_assignment(case):
    v=offer(case);offer(case,1)
    assert accept(case,v).status_code==200
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:offer(case,1)
    assert e.value.status_code==409


def test_snapshot_rejects_later_term_changes(case):
    assert accept(case,offer(case)).status_code==200
    for statement in ("UPDATE service_requests SET preferred_time='12:00'", "UPDATE request_partners SET proposed_price=200 WHERE is_winner"):
        with pytest.raises(psycopg.errors.CheckViolation):case['db'].execute(statement)


def test_historical_rows_readable(case):
    assert case['db'].execute("SELECT agreed_price,status,consent_id FROM reservations WHERE code='HISTORICAL'").fetchone()==(10,'completed',None)


def test_schema_no_consent_no_reservation(case):
    with pytest.raises(psycopg.errors.CheckViolation):
        case['db'].execute("INSERT INTO reservations(code,service_request_id,product_id,partner_id,service_date,passenger_count) VALUES ('UNSAFE',%s,%s,%s,current_date,1)",(uuid4(),uuid4(),uuid4()))


def test_request_expiration(case):
    version = offer(case)
    case['db'].execute("UPDATE service_requests SET expires_at=now()-interval '1 minute'")
    assert accept(case, version).status_code == 409


def test_changed_back_request_still_invalidates(case):
    version = offer(case)
    case['db'].execute("UPDATE service_requests SET additional_notes='temporary change'")
    case['db'].execute('UPDATE service_requests SET additional_notes=NULL')
    assert accept(case, version).status_code == 409


def test_timestamp_is_not_offer_version(case):
    offer(case)
    assert accept(case, '2026-01-01T00:00:00Z').status_code == 422
