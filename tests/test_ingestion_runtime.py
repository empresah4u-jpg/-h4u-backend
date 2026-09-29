"""Actual restricted login, isolated DB: no owner/superuser fixture for APPLY."""
import os
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg import sql,errors
import pytest
from app.db import get_connection
from scripts import apply_ingestion_migration as migration
from pipelines.ingestion_plan import create,InvalidPlan,StalePlan
from pipelines.apply_hotels import apply
from scripts.process_ingestion_embeddings import process_one
from tests.test_ingestion_apply import Encoder


@pytest.fixture(scope='module')
def runtime():
    name=os.environ.get('DB_NAME','')
    assert name.startswith('h4u_ingestion_test_')
    role=os.environ['H4U_TEST_RUNTIME']
    with get_connection() as installer:
        installer.autocommit=True
        installer.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(name),sql.Identifier(role)))
        installer.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        installer.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)))
        installer.execute(migration.migration_sql({k:v for k,v in migration.checksums().items() if k!=migration.NAME}))
        installer.execute(migration.runtime_grants(role))
        installer.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE ON hotels,destinations,data_sources,entity_sources,entity_embeddings TO {}').format(sql.Identifier(role)))
        installer.execute(sql.SQL('GRANT SELECT,INSERT ON entity_source_evidence TO {}').format(sql.Identifier(role)))
        installer.execute(migration.runtime_verify(role))
        destination=installer.execute('SELECT code FROM destinations LIMIT 1').fetchone()[0]
        source=str(uuid4())
        installer.execute("INSERT INTO data_sources(id,name,source_type,url,status) VALUES (%s,'Restricted runtime fictional source','official','https://runtime.example.invalid/','active')",(source,))
        with psycopg.connect(host=os.environ['DB_HOST'],port=os.environ['DB_PORT'],dbname=name,user=role,password=os.environ['H4U_TEST_RUNTIME_PASSWORD'],autocommit=True) as conn:
            yield conn,installer,destination,source


def data(fixture,note='Fictitious observation'):
    token=uuid4().hex[:12]
    return dict(code='RT_'+token,name='Fictional runtime hotel '+token,destination=fixture[2],
        source_id=fixture[3],source_url='https://runtime.example.invalid/',metadata={'note':note})


def snapshot(conn):
    return tuple(conn.execute(sql.SQL("SELECT count(*),md5(coalesce(string_agg(to_jsonb(t)::text,'' ORDER BY id),'')) FROM {} t").format(sql.Identifier(t))).fetchone() for t in ('hotels','entity_sources','entity_source_evidence','ingestion_plans','ingestion_embedding_jobs'))


def test_privileged_function_is_narrow_and_public_cannot_execute(runtime):
    conn,installer,_,_=runtime
    row=installer.execute("SELECT pronargs,prosecdef,proconfig,proowner=(SELECT oid FROM pg_roles WHERE rolname=current_user),prosrc FROM pg_proc WHERE oid='public.lock_ingestion_source_evidence()'::regprocedure").fetchone()
    assert row[:4]==(0,True,['search_path=pg_catalog, pg_temp'],True)
    assert row[4].strip()=='BEGIN\n    LOCK TABLE public.entity_source_evidence IN SHARE ROW EXCLUSIVE MODE;\nEND'
    assert installer.execute("SELECT count(*) FROM pg_proc p CROSS JOIN LATERAL aclexplode(p.proacl) a WHERE p.oid='lock_ingestion_source_evidence()'::regprocedure AND a.grantee=0").fetchone()==(0,)
    with conn.transaction(force_rollback=True):
        conn.execute('SELECT lock_ingestion_source_evidence()')
        assert conn.execute("SELECT relation::regclass::text FROM pg_locks WHERE pid=pg_backend_pid() AND mode='ShareRowExclusiveLock' AND granted").fetchall()==[('entity_source_evidence',)]


@pytest.mark.parametrize('statement',[
    'LOCK TABLE entity_source_evidence IN SHARE ROW EXCLUSIVE MODE',
    "UPDATE entity_source_evidence SET notes='forbidden'",
    'DELETE FROM entity_source_evidence',
    'TRUNCATE entity_source_evidence',
    'ALTER TABLE entity_source_evidence ADD COLUMN forbidden integer',
    'DROP TABLE entity_source_evidence',
    'CREATE TABLE public.forbidden_runtime_table(id integer)',
])
def test_direct_privileged_operations_denied(runtime,statement):
    with pytest.raises(errors.InsufficientPrivilege):
        with runtime[0].transaction(force_rollback=True):
            runtime[0].execute(statement)


def test_effective_privileges_are_minimal(runtime):
    conn=runtime[0]
    assert conn.execute("SELECT rolsuper,rolcreatedb,rolcreaterole,rolinherit FROM pg_roles WHERE rolname=current_user").fetchone()==(False,False,False,False)
    for priv in ('UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'):
        assert conn.execute("SELECT has_table_privilege(current_user,'entity_source_evidence',%s)",(priv,)).fetchone()==(False,)
    for priv in ('SELECT','INSERT'):
        assert conn.execute("SELECT has_table_privilege(current_user,'entity_source_evidence',%s)",(priv,)).fetchone()==(True,)


def test_restricted_apply_durable_idempotent_and_job_retry(runtime):
    conn=runtime[0];row=data(runtime);p=create(conn,[row]);before=snapshot(conn)
    assert apply(conn,p)['mode']=='dry-run' and snapshot(conn)==before
    result=apply(conn,p,dry_run=False)
    assert result['result']['embedding_jobs']==1
    assert conn.execute('SELECT status FROM ingestion_plans WHERE id=%s',(p['plan_id'],)).fetchone()==('applied',)
    after=snapshot(conn)
    assert apply(conn,p,dry_run=False)['result']==result['result'] and snapshot(conn)==after
    other=create(conn,[dict(row,phone='123')],p['plan_id'])
    with pytest.raises(InvalidPlan):apply(conn,other,dry_run=False)
    job=conn.execute('SELECT id FROM ingestion_embedding_jobs WHERE ingestion_plan_id=%s',(p['plan_id'],)).fetchone()[0]
    def fail():raise RuntimeError('private detail')
    assert process_one(conn,job_id=job,model_factory=fail)['status']=='failed'
    assert process_one(conn,job_id=job,model_factory=Encoder)['status']=='completed'


def test_restricted_stale_has_no_writes(runtime):
    conn,installer,_,_=runtime;p=create(conn,[data(runtime)])
    installer.execute("UPDATE data_sources SET name=name||' revised' WHERE id=%s",(runtime[3],))
    before=snapshot(conn)
    with pytest.raises(StalePlan):apply(conn,p,dry_run=False)
    assert snapshot(conn)==before


def test_error_after_lock_rolls_back_everything(runtime):
    conn,installer,_,_=runtime
    installer.execute("""CREATE FUNCTION test_reject_evidence() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        IF NEW.notes='Deliberate isolated failure' THEN RAISE EXCEPTION 'Synthetic rollback test'; END IF; RETURN NEW; END $$;
        CREATE TRIGGER test_reject_evidence BEFORE INSERT ON entity_source_evidence FOR EACH ROW EXECUTE FUNCTION test_reject_evidence();""")
    p=create(conn,[data(runtime,'Deliberate isolated failure')]);before=snapshot(conn)
    with pytest.raises(psycopg.Error):apply(conn,p,dry_run=False)
    assert snapshot(conn)==before
    assert conn.execute("SELECT count(*) FROM pg_locks WHERE pid=pg_backend_pid() AND mode='ShareRowExclusiveLock'").fetchone()==(0,)


def test_official_demo_routes_011(monkeypatch):
    import json
    from scripts import setup_demo
    calls=[];expected=migration.checksums()
    monkeypatch.setattr(setup_demo,'check_marker',lambda:None)
    monkeypatch.setattr(setup_demo,'sql',lambda *args:json.dumps({k:v for k,v in expected.items() if k!=migration.NAME}))
    monkeypatch.setattr(migration,'run_demo',lambda persist=False:calls.append(persist))
    setup_demo.upgrade_demo_schema(expected)
    assert calls==[True]


def test_deployment_refuses_checksum_drift():
    ledger=migration.checksums();ledger[migration.NAME]='wrong'
    with pytest.raises(RuntimeError,match='checksum mismatch'):migration.migration_sql(ledger)
