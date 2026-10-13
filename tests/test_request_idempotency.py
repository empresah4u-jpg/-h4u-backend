"""Real JWT/runtime, committed concurrency in runner-owned disposable DB only."""
import os
from datetime import date, timedelta, datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
import pytest
import psycopg
from fastapi import HTTPException
from app.db import get_admin_connection
from app.routers.service_requests import ServiceRequestCreate
from app.services.request_idempotency import validate_destination_time, fingerprint
from scripts.apply_request_idempotency import apply
from tests.test_commercial_e2e import e2e


@pytest.fixture(scope='module', autouse=True)
def install():
    assert os.getenv('DB_NAME','').startswith('h4u_ingestion_test_')
    with get_admin_connection() as c:
        before=c.execute('SELECT count(*) FROM service_requests').fetchone()
        with c.transaction(force_rollback=True):
            apply(c,os.environ['H4U_TEST_RUNTIME'])
        assert c.execute("SELECT to_regclass('service_request_idempotency')").fetchone()==(None,)
        apply(c,os.environ['H4U_TEST_RUNTIME'])
        apply(c,os.environ['H4U_TEST_RUNTIME'])
        assert c.execute('SELECT count(*) FROM service_requests').fetchone()==before


def body(c):
    return dict(session_id=str(c['users']['tourist']['session']), product_id=str(c['product']),
                service_date=str(date.today()+timedelta(days=7)),passenger_count=1,adults_count=1,minors_count=0)


def post(c, payload=None, key='attempt', owner='tourist'):
    return c['client'].post('/service-requests',json=payload or body(c),
        headers={**c['headers'][owner], 'Idempotency-Key':key})


def counts(c):
    return tuple(c['db'].execute('SELECT (SELECT count(*) FROM service_requests WHERE product_id=%s),(SELECT count(*) FROM service_request_idempotency WHERE traveler_id=%s)',(c['product'],c['users']['tourist']['traveler'])).fetchone())


def test_original_response_and_replay_after_changes(e2e):
    c=e2e;r=post(c);assert r.status_code==201,r.text
    assert r.json()['candidate_count']==2
    c['db'].execute("UPDATE products SET status='draft' WHERE id=%s",(c['product'],))
    c['db'].execute("UPDATE sessions SET status='closed',ended_at=now() WHERE id=%s",(c['users']['tourist']['session'],))
    assert post(c).json()==r.json()
    assert counts(c)==(1,1)
    assert post(c,{**body(c),'additional_notes':'different'}).status_code==409


def race(fn1,fn2):
    barrier=Barrier(2)
    def invoke(fn): barrier.wait(timeout=10);return fn()
    with ThreadPoolExecutor(2) as pool:
        futures=[pool.submit(invoke,fn) for fn in (fn1,fn2)]
        return [f.result(timeout=30) for f in futures]


def test_same_key_concurrency(e2e):
    c=e2e;rs=race(lambda:post(c),lambda:post(c))
    assert [r.status_code for r in rs]==[201,201], [r.text for r in rs]
    assert rs[0].json()==rs[1].json();assert counts(c)==(1,1)


def test_conflicting_key_concurrency(e2e):
    c=e2e;rs=race(lambda:post(c),lambda:post(c,{**body(c),'additional_notes':'other'}))
    assert sorted(r.status_code for r in rs)==[201,409];assert counts(c)==(1,1)


@pytest.mark.parametrize('failure',['partners','product','destination','date','timezone','session','missing','jwt','key'])
def test_rejections_leave_no_rows(e2e,failure):
    c=e2e;p=body(c);expected=409;owner='tourist'
    if failure=='partners':c['db'].execute("UPDATE product_partners SET status='inactive' WHERE product_id=%s",(c['product'],))
    if failure=='product':c['db'].execute('UPDATE products SET reservations_enabled=false WHERE id=%s',(c['product'],))
    if failure=='destination':
        d=c['db'].execute("INSERT INTO destinations(code,name,slug) VALUES (%s,'Other',%s) RETURNING id",(uuid4().hex[:20],uuid4().hex)).fetchone()[0]
        c['db'].execute('UPDATE products SET destination_id=%s WHERE id=%s',(d,c['product']));expected=400
    if failure=='date':p['service_date']=str(date.today()-timedelta(days=2));expected=422
    if failure=='timezone':c['db'].execute("UPDATE destinations SET timezone='invalid' WHERE id=%s",(c['destination'],))
    if failure=='session':owner='other';expected=403
    if failure=='missing':p['session_id']=str(uuid4());expected=403
    if failure=='jwt':c['headers']['tourist']={'Authorization':'Bearer invalid'};expected=401
    if failure=='key':
        r=c['client'].post('/service-requests',json=p,headers=c['headers']['tourist']);expected=422
    else:r=post(c,p,owner=owner)
    assert r.status_code==expected,r.text;assert counts(c)==(0,0)


def test_atomic_failure_and_retry(e2e):
    c=e2e
    c['db'].execute("""CREATE FUNCTION test_fail_attempt() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fixture failure'; END $$;
        CREATE TRIGGER test_fail_attempt BEFORE INSERT ON service_request_idempotency FOR EACH ROW EXECUTE FUNCTION test_fail_attempt();""")
    try:
        assert post(c).status_code==500
        assert counts(c)==(0,0)
        assert c['db'].execute('SELECT count(*) FROM request_partners rp JOIN service_requests sr ON sr.id=rp.service_request_id WHERE sr.product_id=%s',(c['product'],)).fetchone()==(0,)
    finally:c['db'].execute('DROP TRIGGER test_fail_attempt ON service_request_idempotency; DROP FUNCTION test_fail_attempt()')
    assert post(c).status_code==201


def test_sessions_concurrent_and_identity(e2e):
    c=e2e;code=c['db'].execute('UPDATE destinations SET code=upper(code) WHERE id=%s RETURNING code',(c['destination'],)).fetchone()[0]
    def get(owner='tourist',extra=None):return c['client'].post('/commercial-sessions',json={'destination':code,**(extra or {})},headers=c['headers'][owner])
    responses=race(get,get);assert [r.status_code for r in responses]==[200,200]
    assert responses[0].json()==responses[1].json()
    assert get('other').json()['session_id']!=responses[0].json()['session_id']
    assert get(extra={'traveler_id':str(c['users']['other']['traveler'])}).status_code==422
    assert get('A').status_code==403


def test_runtime_permissions(e2e):
    c=e2e;assert post(c).status_code==201
    for statement in ['UPDATE service_request_idempotency SET response_status=201','DELETE FROM service_request_idempotency','TRUNCATE service_request_idempotency']:
        with c['runtime']() as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):conn.execute(statement)
    assert c['db'].execute("SELECT tableowner=current_user FROM pg_tables WHERE tablename='service_request_idempotency'").fetchone()==(True,)
    with pytest.raises(psycopg.Error):c['db'].execute('DELETE FROM service_request_idempotency')


def test_timezone_and_fingerprint():
    p=ServiceRequestCreate(session_id=uuid4(),product_id=uuid4(),service_date=date(2030,1,1),preferred_time='23:30',passenger_count=1,adults_count=1,minors_count=0)
    assert fingerprint(p)==fingerprint(p.model_copy())
    assert fingerprint(p)!=fingerprint(p.model_copy(update={'additional_notes':'changed'}))
    validate_destination_time(p,'America/Lima',datetime(2030,1,2,4,tzinfo=timezone.utc))
    with pytest.raises(HTTPException):validate_destination_time(p,'America/Lima',datetime(2030,1,2,5,tzinfo=timezone.utc))
    with pytest.raises(HTTPException):validate_destination_time(p,'bad/zone')


def process_create(payload, key, gate, output):
    from contextlib import contextmanager
    from app.routers import service_requests as sr
    @contextmanager
    def connection():
        with psycopg.connect(host=os.environ['DB_HOST'],port=os.environ['DB_PORT'],dbname=os.environ['DB_NAME'],user=os.environ['H4U_TEST_RUNTIME'],password=os.environ['H4U_TEST_RUNTIME_PASSWORD']) as c:
            c.execute("SET lock_timeout='10s'");c.commit();yield c
    sr.get_connection=connection
    gate.wait(20)
    try:output.put(('ok',sr.create_service_request(sr.ServiceRequestCreate(**payload),key)))
    except Exception as exc:output.put(('error',type(exc).__name__))


def test_distinct_processes_one_request(e2e):
    import multiprocessing
    ctx=multiprocessing.get_context('spawn');gate=ctx.Barrier(2);queue=ctx.Queue()
    workers=[ctx.Process(target=process_create,args=(body(e2e),'process-race',gate,queue)) for _ in range(2)]
    try:
        for worker in workers:worker.start()
        results=[queue.get(timeout=40) for _ in workers]
        for worker in workers:worker.join(10);assert worker.exitcode==0
        assert [r[0] for r in results]==['ok','ok'],results
        assert results[0][1]==results[1][1];assert counts(e2e)==(1,1)
    finally:
        for worker in workers:
            if worker.is_alive():worker.terminate();worker.join(5)


def test_expired_token_and_payload(e2e):
    c=e2e
    c['db'].execute('UPDATE auth_sessions SET created_at=now()-interval \'2 hours\',expires_at=now()-interval \'1 second\' WHERE user_id=%s',(c['users']['tourist']['id'],))
    assert post(c).status_code==401;assert counts(c)==(0,0)


def test_payload_and_partner_membership(e2e):
    c=e2e
    assert post(c,{**body(c),'traveler_id':str(c['users']['other']['traveler'])}).status_code==422
    assert post(c,{**body(c),'passenger_count':0}).status_code==422
    assert post(c,{**body(c),'adults_count':2}).status_code==400
    r=post(c);assert r.status_code==201
    ids=c['db'].execute('SELECT partner_id FROM request_partners WHERE service_request_id=%s',(r.json()['id'],)).fetchall()
    assert {x[0] for x in ids}=={c['partners']['A'],c['partners']['B']}


def test_concurrent_partner_revocation(e2e):
    c=e2e
    # A concurrent updater owns both eligible partners; request must wait and
    # then observe revocation, not distribute from a stale unlocked snapshot.
    with c['db'].transaction():
        c['db'].execute('UPDATE partners SET reservations_enabled=false WHERE id IN (%s,%s)',(c['partners']['A'],c['partners']['B']))
        with ThreadPoolExecutor(1) as pool:
            # Commit explicitly before waiting for the request result.
            future=pool.submit(post,c)
            c['db'].execute('COMMIT')
            r=future.result(timeout=20)
    assert r.status_code==409,r.text;assert counts(c)==(0,0)


def test_keys_are_scoped_to_traveler(e2e):
    c=e2e
    assert post(c).status_code==201
    other={**body(c),'session_id':str(c['users']['other']['session'])}
    r=post(c,other,owner='other');assert r.status_code==201,r.text
    assert c['db'].execute('SELECT count(*) FROM service_requests WHERE product_id=%s',(c['product'],)).fetchone()==(2,)


def test_session_resolution_with_request(e2e):
    c=e2e
    code=c['db'].execute('UPDATE destinations SET code=upper(code) WHERE id=%s RETURNING code',(c['destination'],)).fetchone()[0]
    def resolve():return c['client'].post('/commercial-sessions',json={'destination':code},headers=c['headers']['tourist'])
    sid=resolve().json()['session_id']
    results=race(resolve,lambda:post(c,{**body(c),'session_id':sid}))
    assert [r.status_code for r in results]==[200,201]


def test_dst_and_fixed_time():
    p=ServiceRequestCreate(session_id=uuid4(),product_id=uuid4(),service_date=date(2030,11,3),preferred_time='01:30',passenger_count=1,adults_count=1,minors_count=0)
    with pytest.raises(HTTPException):validate_destination_time(p,'America/New_York',datetime(2030,1,1,tzinfo=timezone.utc))
    with pytest.raises(HTTPException):validate_destination_time(p.model_copy(update={'preferred_time':None,'flexible_time':False}),'America/Lima')


def test_demo_attempt_keys(e2e):
    from app.demo.runner import request_attempt_key
    run_id=uuid4()
    key=request_attempt_key(run_id)
    assert key==request_attempt_key(run_id)
    assert key!=request_attempt_key(uuid4())
    assert key!=request_attempt_key(run_id,'second-request')
    first=post(e2e,key=key)
    assert first.status_code==201
    assert post(e2e,key=request_attempt_key(run_id)).json()==first.json()
    assert counts(e2e)==(1,1)
