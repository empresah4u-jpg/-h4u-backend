"""Admin integration: temporary identities, random credentials, rollback-only business rows."""
import secrets
from uuid import uuid4
from datetime import timedelta
import psycopg
import pytest
from app.db import get_connection
from app.services import administration, partner_memberships
from app.services.admin_governance import governance_lock
from tests.test_commercial import flow, reserve
from tests.test_identity_auth import identity_case, headers
from tests.test_financial_adjustments import commission, settlement


@pytest.fixture
def case(identity_case):
    t=identity_case
    t['admin']=t['user'](role='admin')
    t['admin_headers']=headers(t['token'](t['admin']))
    return t


def body(role='operator', **extra):
    return {'email':uuid4().hex+'@example.invalid','password':secrets.token_urlsafe(24),'role':role,'reason':'test provisioning',**extra}


def state(case,uid,status):
    return case['client'].patch('/admin/users/'+str(uid)+'/status',headers=case['admin_headers'],json={'status':status,'reason':'test status'})


def member(case,user,role='staff',status='active'):
    return case['client'].put('/admin/partners/'+str(case['flow']['partner'])+'/members',headers=case['admin_headers'],json={
        'user_id':str(user['id']),'membership_role':role,'status':status,'reason':'test membership'})


@pytest.mark.parametrize('role,mrole',[('tourist',None),('partner','owner'),('partner','manager'),('partner','staff')])
def test_non_h4u_roles_denied_all_admin_routes(case,role,mrole):
    user=case['user'](role=role,membership_role=mrole or 'staff')
    auth=headers(case['token'](user))
    pid=str(case['flow']['partner'])
    routes=['/admin/me','/admin/users','/admin/users/'+str(user['id']),'/admin/partners','/admin/partners/'+pid,
            '/admin/partners/'+pid+'/members','/admin/reservations','/admin/service-requests']
    for route in routes:
        assert case['client'].get(route,headers=auth).status_code==403
    assert case['client'].post('/admin/users',headers=auth,json=body(role='admin')).status_code==403


def test_admin_reads_safe_fields_and_filters(case):
    t=case
    other=t['user'](role='operator')
    reserve(t['flow'])
    for route in ['/admin/me','/admin/users','/admin/users/'+str(other['id']),'/admin/partners',
                  '/admin/partners/'+str(t['flow']['partner']),'/admin/partners/'+str(t['flow']['partner'])+'/members',
                  '/admin/reservations','/admin/service-requests']:
        response=t['client'].get(route,headers=t['admin_headers'])
        assert response.status_code==200
        assert response.headers['cache-control']=='no-store'
        assert not any(s in response.text for s in ('password_hash','access_token','$argon2','JWT_SECRET'))
    result=t['client'].get('/admin/users?role=operator&limit=1&offset=0',headers=t['admin_headers']).json()
    assert len(result['items'])==1 and result['items'][0]['role']=='operator'
    assert t['client'].get('/admin/users?limit=101',headers=t['admin_headers']).status_code==422
    assert t['client'].get('/admin/users?offset=-1',headers=t['admin_headers']).status_code==422


def test_operator_only_operational_reads_and_no_escalation(case):
    t=case
    user=t['user'](role='operator')
    auth=headers(t['token'](user))
    for route in ('/admin/me','/admin/partners','/admin/reservations','/admin/service-requests'):
        assert t['client'].get(route,headers=auth).status_code==200
    for route in ('/admin/users','/admin/users/'+str(t['admin']['id']),'/admin/partners/'+str(t['flow']['partner'])+'/members'):
        assert t['client'].get(route,headers=auth).status_code==403
    assert t['client'].post('/admin/users',headers=auth,json=body(role='admin')).status_code==403
    assert t['client'].patch('/admin/users/'+str(user['id'])+'/status',headers=auth,json={'status':'active','role':'admin','reason':'test'}).status_code==403
    assert t['client'].post('/refunds',headers=auth,json={}).status_code==403
    assert t['client'].post('/payments/X/confirm-partner',headers=auth).status_code==403
    assert t['client'].post('/settlements/X/verify-payment',headers=auth).status_code==403


def test_disabled_admin_and_anonymous_cannot_access(case):
    t=case
    assert t['client'].get('/admin/me').status_code==401
    t['db'].execute("UPDATE users SET status='disabled' WHERE id=%s",(t['admin']['id'],))
    assert t['client'].get('/admin/me',headers=t['admin_headers']).status_code==401


def test_provision_operator_login_and_duplicate(case):
    t=case
    data=body()
    # Password whitespace must not be normalized by schemas.
    data['password']=' '+data['password']+' '
    response=t['client'].post('/admin/users',headers=t['admin_headers'],json=data)
    assert response.status_code==201
    assert data['password'] not in response.text and 'password' not in response.text
    login=t['client'].post('/auth/login',json={'email':data['email'],'password':data['password']})
    assert login.status_code==200
    assert t['client'].post('/admin/users',headers=t['admin_headers'],json=data).status_code==409
    event=t['db'].execute("SELECT actor_id,new_values FROM admin_events WHERE action='user.created' AND target_id=%s",(response.json()['id'],)).fetchone()
    assert event[0]==t['admin']['id'] and event[1]['role']=='operator'
    assert data['password'] not in str(event[1]) and 'password_hash' not in str(event[1])


@pytest.mark.parametrize('change',[{'role':'admin'},{'role':'tourist'},{'password':'tiny'},{'email':'bad'},{'password_hash':'injected'},{'token_version':0}])
def test_provision_rejects_unsafe_fields_without_echo(case,change):
    data=body(**change)
    result=case['client'].post('/admin/users',headers=case['admin_headers'],json=data)
    assert result.status_code==422
    assert data['password'] not in result.text


def test_provision_partner_and_atomic_membership(case):
    t=case
    data=body(role='partner',partner_id=str(t['flow']['partner']),membership_role='owner')
    response=t['client'].post('/admin/users',headers=t['admin_headers'],json=data)
    assert response.status_code==201
    uid=response.json()['id']
    assert t['db'].execute('SELECT membership_role FROM partner_memberships WHERE user_id=%s',(uid,)).fetchone()==('owner',)
    data=body(role='partner',partner_id=str(uuid4()))
    assert t['client'].post('/admin/users',headers=t['admin_headers'],json=data).status_code==404
    assert t['db'].execute('SELECT count(*) FROM users WHERE email=%s',(data['email'],)).fetchone()[0]==0


def test_membership_changes_and_old_jwt(case):
    t=case
    t['user']()  # second owner preserves administration
    user=t['user']()
    auth=headers(t['token'](user))
    assert member(t,user,'staff').status_code==200
    assert member(t,user,'staff','suspended').status_code==200
    assert t['client'].get('/partners/'+str(t['flow']['partner']),headers=auth).status_code==403
    assert member(t,user,'manager','active').status_code==200
    assert t['client'].get('/partners/'+str(t['flow']['partner']),headers=auth).json()['membership']['membership_role']=='manager'
    assert member(t,user,'manager','revoked').status_code==200
    assert t['client'].get('/partners/'+str(t['flow']['partner']),headers=auth).status_code==403


@pytest.mark.parametrize('role,status',[('staff','active'),('owner','suspended'),('owner','revoked')])
def test_last_owner_cannot_be_removed(case,role,status):
    t=case
    user=t['user']()
    assert member(t,user,role,status).status_code==409
    assert t['db'].execute('SELECT membership_role,status FROM partner_memberships WHERE user_id=%s',(user['id'],)).fetchone()==('owner','active')


def test_disabling_last_owner_denied_then_revocation(case):
    t=case
    user=t['user']()
    auth=headers(t['token'](user))
    assert state(t,user['id'],'disabled').status_code==409
    t['user']()
    assert state(t,user['id'],'locked').status_code==200
    assert t['client'].get('/auth/me',headers=auth).status_code==401
    assert state(t,user['id'],'active').status_code==200
    assert t['client'].get('/auth/me',headers=auth).status_code==401


def test_no_admin_account_mutations(case):
    t=case
    assert state(t,t['admin']['id'],'disabled').status_code==403
    assert t['client'].get('/admin/me',headers=t['admin_headers']).status_code==200


def test_serialized_owner_decisions_and_cross_connection_lock(case):
    t=case
    first,second=t['user'](),t['user']()
    assert member(t,first,'staff').status_code==200
    assert member(t,second,'staff').status_code==409
    with t['db'].cursor() as cur: governance_lock(cur)
    # Another transaction cannot enter ANY admin mutation while the first decides.
    with get_connection() as other:
        acquired=other.execute("SELECT pg_try_advisory_xact_lock(hashtext('h4u-admin-governance'))").fetchone()[0]
        assert acquired is False


def test_partner_provision_profile_and_activation(case):
    t=case
    response=t['client'].post('/admin/partners',headers=t['admin_headers'],json={'code':uuid4().hex[:24],'business_name':'Test business','reason':'test business'})
    assert response.status_code==201 and response.json()['status']=='pending'
    pid=response.json()['id']
    route='/admin/partners/'+pid
    payload={'status':'active','reservations_enabled':True,'reason':'test activation'}
    assert t['client'].patch(route+'/status',headers=t['admin_headers'],json=payload).status_code==409
    user=t['user'](owner=pid)
    assert t['client'].patch(route+'/status',headers=t['admin_headers'],json=payload).status_code==200
    assert t['client'].patch(route,headers=t['admin_headers'],json={'business_name':'New name','reason':'test edit'}).status_code==200
    for name,value in [('id',str(uuid4())),('commission_value',99),('created_at','2020-01-01')]:
        assert t['client'].patch(route,headers=t['admin_headers'],json={name:value,'reason':'test edit'}).status_code==422


def test_suspend_preserves_history_and_debt_blocks_activation(case):
    t=case
    t['user']()
    res,pay,comm=commission(t['flow'])
    s=settlement(t['flow'])
    route='/admin/partners/'+str(t['flow']['partner'])+'/status'
    assert t['client'].patch(route,headers=t['admin_headers'],json={'status':'suspended','reservations_enabled':False,'reason':'manual suspension'}).status_code==200
    assert t['db'].execute('SELECT status FROM payments WHERE code=%s',(pay['code'],)).fetchone()[0]=='paid'
    assert t['db'].execute('SELECT status FROM reservations WHERE code=%s',(res['code'],)).fetchone()[0]=='confirmed'
    t['db'].execute("UPDATE partner_settlements SET status='overdue' WHERE id=%s",(s['id'],))
    assert t['client'].patch(route,headers=t['admin_headers'],json={'status':'active','reservations_enabled':True,'reason':'test reactivation'}).status_code==409


def test_audit_failure_rolls_back_user_and_ledger_immutable(case,monkeypatch):
    t=case
    original=administration.admin_event
    def fail(*args,**kwargs): raise RuntimeError('test failure')
    monkeypatch.setattr(administration,'admin_event',fail)
    data=body()
    assert t['client'].post('/admin/users',headers=t['admin_headers'],json=data).status_code==500
    assert t['db'].execute('SELECT count(*) FROM users WHERE email=%s',(data['email'],)).fetchone()[0]==0
    monkeypatch.setattr(administration,'admin_event',original)
    result=t['client'].post('/admin/users',headers=t['admin_headers'],json=body())
    assert result.status_code==201
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState),t['db'].transaction():
        t['db'].execute('DELETE FROM admin_events WHERE target_id=%s',(result.json()['id'],))
