"""Real JWT + restricted connections + committed races, disposable DB only."""
import os
from contextlib import contextmanager
from datetime import date,timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
import secrets
import sys

import psycopg
from psycopg import sql
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import get_admin_connection
from app.services.passwords import hash_password
from scripts.setup_runtime import grant_table_access


@pytest.fixture
def e2e(monkeypatch):
    assert os.getenv('DB_NAME','').startswith('h4u_ingestion_test_'), 'Disposable DB required'
    role=os.environ['H4U_TEST_RUNTIME']
    db=get_admin_connection();db.autocommit=True
    assert db.execute('SELECT current_database()').fetchone()[0]==os.environ['DB_NAME']
    db.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(os.environ['DB_NAME']),sql.Identifier(role)))
    db.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)))
    grant_table_access(db,role)
    pids=set()
    @contextmanager
    def runtime():
        with psycopg.connect(host=os.environ['DB_HOST'],port=os.environ['DB_PORT'],dbname=os.environ['DB_NAME'],user=role,password=os.environ['H4U_TEST_RUNTIME_PASSWORD']) as c:
            assert c.execute('SELECT current_user').fetchone()==(role,)
            assert c.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()==(False,False,False,False)
            c.execute("SET lock_timeout='5s'; SET statement_timeout='10s'");c.commit()
            pids.add(c.info.backend_pid)
            yield c
    # Every HTTP auth/router/service uses a genuine restricted LOGIN, no actor override.
    for name,module in list(sys.modules.items()):
        if name.startswith('app.') and hasattr(module,'get_connection'):
            monkeypatch.setattr(module,'get_connection',runtime)
    token='TEST-E2E-'+uuid4().hex[:10]
    d=db.execute("INSERT INTO destinations(code,name,slug) VALUES (%s,'TEST destination',%s) RETURNING id",(token,token.lower())).fetchone()[0]
    product=db.execute("INSERT INTO products(destination_id,code,name,slug,product_type,status,reservations_enabled) VALUES (%s,%s,'TEST commercial product',%s,'tour','active',true) RETURNING id",(d,token,token.lower())).fetchone()[0]
    password=secrets.token_urlsafe(32);encoded=hash_password(password)
    users={};partners={}
    for name in ('tourist','other','A','B','inactive','disabled'):
        email=token+'-'+name+'@example.invalid'
        if name in ('tourist','other'):
            traveler=db.execute('INSERT INTO travelers DEFAULT VALUES RETURNING id').fetchone()[0]
            uid=db.execute("INSERT INTO users(email,password_hash,role,traveler_id) VALUES (%s,%s,'tourist',%s) RETURNING id",(email,encoded,traveler)).fetchone()[0]
            session=db.execute("INSERT INTO sessions(traveler_id,destination_id,channel) VALUES (%s,%s,'test') RETURNING id",(traveler,d)).fetchone()[0]
            users[name]=dict(id=uid,email=email,traveler=traveler,session=session)
        else:
            partner=db.execute("INSERT INTO partners(code,business_name,status,reservations_enabled) VALUES (%s,'TEST provider',%s,%s) RETURNING id",(token+name,'inactive' if name=='inactive' else 'active',name!='disabled')).fetchone()[0]
            partners[name]=partner
            db.execute('INSERT INTO product_partners(product_id,partner_id,partner_price) VALUES (%s,%s,999)',(product,partner))
            uid=db.execute("INSERT INTO users(email,password_hash,role,partner_id) VALUES (%s,%s,'partner',%s) RETURNING id",(email,encoded,partner)).fetchone()[0]
            db.execute("INSERT INTO partner_memberships(partner_id,user_id,membership_role,status) VALUES (%s,%s,'owner','active')",(partner,uid))
            users[name]=dict(id=uid,email=email)
    try:
        with TestClient(app) as client:
            headers={}
            for name,u in users.items():
                login=client.post('/auth/login',json={'email':u['email'],'password':password})
                assert login.status_code==200
                headers[name]={'Authorization':'Bearer '+login.json()['access_token']}
            yield dict(db=db,client=client,headers=headers,users=users,partners=partners,product=product,destination=d,runtime=runtime,pids=pids)
    finally:
        db.close()
    # All committed TEST rows are confined to the runner-owned disposable DB.


def request(c,owner='tourist'):
    body=dict(session_id=str(c['users'][owner]['session']),product_id=str(c['product']),service_date=str(date.today()+timedelta(days=7)),preferred_time='09:00',flexible_time=True,passenger_count=2,adults_count=2,minors_count=0,additional_notes='TEST: two adults, no transfer')
    r=c['client'].post('/service-requests',json=body,headers=c['headers'][owner]);assert r.status_code==201,r.text
    req=r.json()
    rows=c['db'].execute('SELECT id,partner_id FROM request_partners WHERE service_request_id=%s',(req['id'],)).fetchall()
    assert {row[1] for row in rows}=={c['partners']['A'],c['partners']['B']}
    req['candidate']={name:str(next(row[0] for row in rows if row[1]==c['partners'][name])) for name in ('A','B')}
    return req


def offer(c,req,name):
    body=dict(request_partner_id=req['candidate'][name],action='counter_offer',proposed_price=90 if name=='A' else 100,proposed_currency='PEN',proposed_time='10:00' if name=='A' else '11:00',partner_message='TEST: total for two adults; no transfers; agreed boat service')
    r=c['client'].post('/partner-responses',json=body,headers=c['headers'][name]);assert r.status_code==200,r.text
    assert not r.json()['winner']
    r=c['client'].get('/partner-responses/'+req['candidate'][name]+'/counter-offer',headers=c['headers']['tourist']);assert r.status_code==200,r.text
    return r.json()


def accept(c,req,name,version,owner='tourist',**extra):
    return c['client'].post('/partner-responses/'+req['candidate'][name]+'/accept-counter-offer',headers=c['headers'][owner],json=dict(expected_version=version,**extra))


def test_authenticated_full_flow(e2e):
    c=e2e;req=request(c);a=offer(c,req,'A');b=offer(c,req,'B')
    assert a['version']==b['version']==1
    db=c['db'];rid=req['id']
    assert db.execute('SELECT product_id,destination_id,assigned_partner_id,status FROM service_requests WHERE id=%s',(rid,)).fetchone()==(c['product'],c['destination'],None,'offers_received')
    assert db.execute('SELECT count(*) FROM request_offer_consents WHERE service_request_id=%s',(rid,)).fetchone()==(0,)
    for change in ({},{'proposed_price':1},{'proposed_currency':'USD'},{'proposed_time':'23:00'}):
        body=dict(request_partner_id=req['candidate']['A'],action='accept',**change)
        assert c['client'].post('/partner-responses',json=body,headers=c['headers']['A']).status_code==409
    assert accept(c,req,'A',a['version'],'other').status_code==403
    assert accept(c,req,'A',99).status_code==409
    assert accept(c,req,'A',a['version'],amount_total=1,currency='USD').status_code==422
    foreign=request(c,'other')
    # Same server must enforce ownership of a candidature from another request.
    assert accept(c,foreign,'A',1).status_code==403
    result=accept(c,req,'A',a['version']);assert result.status_code==200,result.text
    again=accept(c,req,'A',a['version']);assert again.status_code==200 and again.json()['replayed']
    assert again.json()['consent_id']==result.json()['consent_id']
    snapshot=db.execute('SELECT user_id,traveler_id,partner_id,offer_version,request_revision,amount_total,currency,service_time,passenger_count,conditions FROM request_offer_consents WHERE service_request_id=%s',(rid,)).fetchone()
    assert snapshot[:7]==(c['users']['tourist']['id'],c['users']['tourist']['traveler'],c['partners']['A'],1,1,90,'PEN')
    assert str(snapshot[7])=='10:00:00' and snapshot[8]==2 and snapshot[9]==a['message']
    assert db.execute('SELECT assigned_partner_id,status FROM service_requests WHERE id=%s',(rid,)).fetchone()==(c['partners']['A'],'partner_assigned')
    assert db.execute('SELECT status,is_winner FROM request_partners WHERE id=%s',(req['candidate']['B'],)).fetchone()==('lost',False)
    reservation=c['client'].post('/reservations',headers=c['headers']['tourist'],json={'service_request_code':req['code']});assert reservation.status_code==201,reservation.text
    res=reservation.json()
    original=db.execute('SELECT consent_id,partner_id,agreed_price,currency,service_date,service_time,passenger_count FROM reservations WHERE id=%s',(res['id'],)).fetchone()
    assert str(original[0])==result.json()['consent_id'] and original[1:4]==(c['partners']['A'],90,'PEN')
    assert original[4]==date.today()+timedelta(days=7) and str(original[5])=='10:00:00' and original[6]==2
    db.execute('UPDATE product_partners SET partner_price=777 WHERE product_id=%s',(c['product'],))
    assert db.execute('SELECT consent_id,partner_id,agreed_price,currency,service_date,service_time,passenger_count FROM reservations WHERE id=%s',(res['id'],)).fetchone()==original
    with c['runtime']() as runtime:
        for statement in ('UPDATE request_offer_consents SET amount_total=1','DELETE FROM request_offer_consents','TRUNCATE request_offer_consents CASCADE'):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with runtime.transaction():runtime.execute(statement)


def test_material_change_republish(e2e):
    c=e2e;req=request(c);old=offer(c,req,'A')
    c['db'].execute("UPDATE service_requests SET additional_notes='TEST revised conditions' WHERE id=%s",(req['id'],))
    assert accept(c,req,'A',old['version']).status_code==409
    new=offer(c,req,'A');assert new['version']>old['version']
    assert accept(c,req,'A',new['version']).status_code==200
    assert c['db'].execute('SELECT request_revision FROM request_offer_consents WHERE service_request_id=%s',(req['id'],)).fetchone()==(2,)


@pytest.mark.parametrize('state',['cancelled','expired'])
def test_closed_request(e2e,state):
    c=e2e;req=request(c);a=offer(c,req,'A')
    c['db'].execute('UPDATE service_requests SET status=%s WHERE id=%s',(state,req['id']))
    assert accept(c,req,'A',a['version']).status_code==409


def test_two_authenticated_acceptances_and_reservations_race(e2e):
    c=e2e;req=request(c);versions={n:offer(c,req,n)['version'] for n in ('A','B')};barrier=Barrier(2)
    def attempt(n):
        barrier.wait(timeout=10)
        return accept(c,req,n,versions[n])
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(attempt,('A','B')))
    assert sorted(r.status_code for r in results)==[200,409]
    db=c['db'];rid=req['id']
    assert db.execute('SELECT count(*) FROM request_partners WHERE service_request_id=%s AND is_winner',(rid,)).fetchone()==(1,)
    assert db.execute('SELECT count(*) FROM request_offer_consents WHERE service_request_id=%s',(rid,)).fetchone()==(1,)
    assert db.execute("SELECT indisunique,indisvalid FROM pg_index WHERE indexrelid='uq_request_partners_one_winner'::regclass").fetchone()==(True,True)
    barrier=Barrier(2)
    def reserve(_):
        barrier.wait(timeout=10)
        return c['client'].post('/reservations',headers=c['headers']['tourist'],json={'service_request_code':req['code']})
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(reserve,range(2)))
    assert sorted(r.status_code for r in results)==[201,409]
    assert db.execute('SELECT count(*) FROM reservations r JOIN request_offer_consents c ON c.id=r.consent_id WHERE r.service_request_id=%s',(rid,)).fetchone()==(1,)
    assert len(c['pids'])>2
