"""Synthetic reservations; genuine JWT and restricted runtime; disposable DB."""
from uuid import uuid4
import pytest
import psycopg
from pydantic import ValidationError
from app.auth import Principal
from tests.test_commercial_e2e import e2e, request, accept  # noqa: F401


def reserve(c,owner='tourist'):
    req=request(c,owner)
    result=c['client'].post('/partner-responses',headers=c['headers']['A'],json=dict(
        request_partner_id=req['candidate']['A'],action='counter_offer',proposed_price='90.25',
        proposed_currency='PEN',proposed_time='10:00',partner_message='Synthetic terms'))
    assert result.status_code==200
    version=c['db'].execute('SELECT offer_version FROM request_partners WHERE id=%s',(req['candidate']['A'],)).fetchone()[0]
    assert accept(c,req,'A',version,owner).status_code==200
    response=c['client'].post('/reservations',headers=c['headers'][owner],json={'service_request_code':req['code']})
    assert response.status_code==201
    return req,response.json()


def read(c,path='',owner='tourist'):
    return c['client'].get('/reservations'+path,headers=c['headers'][owner])


def test_owner_list_detail_relation_price_and_pagination(e2e):
    c=e2e
    assert read(c).json()=={'items':[],'has_more':False}
    pending=request(c)
    assert c['client'].get('/service-requests/'+pending['id'],headers=c['headers']['tourist']).json()['reservations']==[]
    req,first=reserve(c);_,second=reserve(c);foreign_req,foreign=reserve(c,'other')
    page=read(c,'?limit=1');assert page.status_code==200 and page.headers['cache-control']=='no-store'
    assert page.json()['has_more'] and page.json()['items'][0]['id']==second['id']
    assert read(c,'?limit=1&offset=1').json()['items'][0]['id']==first['id']
    detail=read(c,'/'+first['id']).json()
    assert detail['service_request']=={'id':req['id'],'code':req['code']}
    assert detail['agreed_price']=='90.25' and detail['currency']=='PEN' and detail['service_time']=='10:00:00'
    assert detail['passenger_count']==2 and detail['provider']=={'name':'TEST provider'}
    assert set(detail)=={'id','code','service_request','product','provider','service_date','service_time','passenger_count','agreed_price','currency','status','created_at'}
    c['db'].execute('UPDATE product_partners SET partner_price=999 WHERE product_id=%s',(c['product'],))
    assert read(c,'/'+first['id']).json()['agreed_price']=='90.25'
    assert read(c,'/'+foreign['id']).status_code==404
    assert c['client'].get('/service-requests/'+foreign_req['id'],headers=c['headers']['tourist']).status_code==404
    assert read(c,'/'+str(uuid4())).status_code==404
    assert foreign['id'] not in read(c).text
    for suffix in ('?traveler_id='+str(c['users']['other']['traveler']),'?limit=101','?offset=-1','/'+first['id']+'?traveler_id=x'):
        assert read(c,suffix).status_code==422
    assert read(c,owner='A').status_code==403


def test_actual_states_and_no_payment_inference(e2e):
    c=e2e
    before=c['db'].execute('SELECT count(*) FROM payments').fetchone()[0]
    for status in ('pending','awaiting_passenger_data','payment_pending','confirmed','ready','completed','cancelled','no_show','expired'):
        _,reservation=reserve(c)  # Each terminal state uses its own synthetic reservation.
        c['db'].execute('UPDATE reservations SET status=%s WHERE id=%s',(status,reservation['id']))
        data=read(c,'/'+reservation['id']).json()
        assert data['status']==status and 'paid' not in data and 'payments' not in data
    assert c['db'].execute('SELECT count(*) FROM payments').fetchone()[0]==before


def test_authentication_identity_and_runtime(e2e):
    c=e2e
    assert c['client'].get('/reservations').status_code==401
    assert c['client'].get('/reservations',headers={'Authorization':'Bearer expired.invalid.signature'}).status_code==401
    with pytest.raises(ValidationError):Principal(subject=str(c['users']['tourist']['id']),role='tourist',traveler_id=None)
    with pytest.raises(psycopg.errors.CheckViolation):c['db'].execute('UPDATE users SET traveler_id=NULL WHERE id=%s',(c['users']['tourist']['id'],))
    with c['runtime']() as conn:
        assert conn.execute("SELECT has_table_privilege(current_user,'reservations','SELECT')").fetchone()==(True,)
    c['db'].execute("UPDATE auth_sessions SET created_at=now()-interval '2 hours',expires_at=now()-interval '1 second' WHERE user_id=%s",(c['users']['tourist']['id'],))
    assert read(c).status_code==401
