"""Membership policy through real JWTs and isolated identity fixtures."""
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi import HTTPException
from app.auth import Principal
from app.services import partner_memberships as memberships
from app.routers import partner_responses as responses, reservations, settlements
from tests.test_commercial import flow, request, reserve, passenger
from tests.test_identity_auth import identity_case, headers
from tests.test_financial_adjustments import commission, settlement, settle


def principal(user):
    return Principal(subject=str(user['id']), role=user['role'])


def admin(case):
    return principal(case['user'](role='admin'))


def partner(case):
    return case['db'].execute("INSERT INTO partners(code,business_name,status,reservations_enabled) VALUES (%s,'Test business','active',true) RETURNING id", (uuid4().hex[:24],)).fetchone()[0]


def path(case, suffix='', pid=None):
    return '/partners/'+str(pid or case['flow']['partner'])+suffix


@pytest.mark.parametrize('suffix', ['', '/membership', '/products', '/requests', '/reservations'])
def test_member_reads_own_and_never_foreign(identity_case, suffix):
    t = identity_case
    own = t['user']()
    auth = headers(t['token'](own))
    assert t['client'].get(path(t,suffix), headers=auth).status_code == 200
    assert t['client'].get(path(t,suffix,pid=partner(t)), headers=auth).status_code == 403
    assert t['client'].get(path(t,suffix,pid=uuid4()), headers=auth).status_code == 403
    assert t['client'].get(path(t,suffix)).status_code == 401


def test_no_membership_not_legacy_id_or_claims(identity_case):
    t = identity_case
    user = t['user'](membership=False)
    auth = headers(t['token'](user))
    auth.update({'X-Partner-Id':str(t['flow']['partner']), 'X-Membership-Role':'owner','X-Role':'admin'})
    assert t['client'].get('/partners',headers=auth).status_code == 403
    assert t['client'].get(path(t),headers=auth).status_code == 403
    req = request(t['flow'])
    assert t['client'].post('/partner-responses',headers=auth,json={
        'request_partner_id':req['candidates'][0]['request_partner_id'],'action':'accept'}).status_code == 403


@pytest.mark.parametrize('new_status', ['suspended','revoked'])
def test_old_jwt_loses_business_access_immediately(identity_case, new_status):
    t = identity_case
    t['user']()  # Another effective owner; removal of the last is now forbidden.
    user = t['user']()
    auth = headers(t['token'](user))
    actor = admin(t)
    res = reserve(t['flow'])
    assert t['client'].get(path(t),headers=auth).status_code == 200
    memberships.set_membership(actor,user['id'],t['flow']['partner'],'owner',new_status)
    assert t['client'].get('/auth/me',headers=auth).status_code == 200  # identity remains valid
    assert t['client'].get(path(t),headers=auth).status_code == 403
    assert t['client'].post('/reservations/'+res['code']+'/cancel',headers=auth,json={'reason':'test'}).status_code == 403
    memberships.set_membership(actor,user['id'],t['flow']['partner'],'owner','active')
    assert t['client'].get(path(t),headers=auth).status_code == 200


def test_disabled_user_is_rejected(identity_case):
    t = identity_case
    user = t['user']()
    auth = headers(t['token'](user))
    t['db'].execute("UPDATE users SET status='disabled' WHERE id=%s",(user['id'],))
    assert t['client'].get(path(t),headers=auth).status_code == 401


def test_many_users_one_business_and_one_user_many_businesses(identity_case):
    t = identity_case
    first, second = t['user'](), t['user'](membership_role='staff')
    other = partner(t)
    memberships.set_membership(admin(t),first['id'],other,'manager')
    auth = headers(t['token'](first))
    items = t['client'].get('/partners',headers=auth).json()['items']
    assert {i['id'] for i in items} == {str(other),str(t['flow']['partner'])}
    assert t['client'].get(path(t,pid=other),headers=auth).json()['membership']['membership_role']=='manager'
    other_auth = headers(t['token'](second))
    assert t['client'].get(path(t),headers=other_auth).status_code == 200
    assert t['client'].get(path(t,pid=other),headers=other_auth).status_code == 403


@pytest.mark.parametrize('role', ['owner','manager','staff'])
def test_business_roles_are_never_h4u_staff(identity_case, role):
    t = identity_case
    user = t['user'](membership_role=role)
    auth = headers(t['token'](user))
    for route in ('/refunds','/settlements','/settlements/X/verify-payment','/commissions/from-payment/X'):
        assert t['client'].post(route,headers=auth,json={}).status_code == 403
    with pytest.raises(HTTPException) as denied:
        memberships.set_membership(Principal(subject=str(user['id']),role='partner',partner_id=t['flow']['partner']),
            user['id'],t['flow']['partner'],'owner')
    assert denied.value.status_code == 403


def test_staff_cannot_use_owner_or_manager_permissions(identity_case):
    t = identity_case
    user = t['user'](membership_role='staff')
    actor = Principal(subject=str(user['id']),role='partner',partner_id=t['flow']['partner'])
    for check in (memberships.require_partner_manager,memberships.require_partner_owner):
        with t['db'].cursor() as cur, pytest.raises(HTTPException) as denied:
            check(cur,actor,t['flow']['partner'])
        assert denied.value.status_code == 403
    commission(t['flow'])
    s = settlement(t['flow'])
    assert t['client'].post('/settlements/'+s['code']+'/report-payment',headers=headers(t['token'](user)),json={'payment_method':'cash'}).status_code == 403


@pytest.mark.parametrize('role', ['owner','manager','staff'])
def test_members_operate_commercial_flow(identity_case, role):
    t = identity_case
    from app.routers import passengers, payments
    req = request(t['flow'])
    auth = headers(t['token'](t['user'](membership_role=role)))
    assert t['client'].post('/partner-responses',headers=auth,json={
        'request_partner_id':req['candidates'][0]['request_partner_id'],'action':'accept'}).status_code == 200
    res = t['client'].post('/reservations',headers=auth,json={'service_request_code':req['code']})
    assert res.status_code == 201
    code = res.json()['code']
    assert t['client'].post('/reservations/'+code+'/passengers',headers=auth,json={'passengers':[passenger().model_dump(mode='json')]}).status_code == 201
    pay = t['client'].post('/payments',headers=auth,json={'reservation_code':code,'payment_method':'cash','received_by':'partner'})
    assert pay.status_code == 201
    assert t['client'].post('/payments/'+pay.json()['code']+'/confirm-partner',headers=auth).status_code == 200
    tourist = headers(t['token'](t['user'](role='tourist')))
    assert t['client'].post('/payments/'+pay.json()['code']+'/confirm-customer',headers=tourist).json()['paid'] is True


def test_admin_provisions_and_events_preserve_history(identity_case):
    t = identity_case
    actor = admin(t)
    user = t['user'](membership=False)
    pid = t['flow']['partner']
    ids = []
    for role,status in [('staff','active'),('manager','active'),('manager','suspended'),('manager','revoked'),('owner','active')]:
        ids.append(memberships.set_membership(actor,user['id'],pid,role,status)['id'])
    assert len(set(ids)) == 1
    rows = t['db'].execute('SELECT event_type,actor_subject FROM partner_events WHERE membership_id=%s ORDER BY created_at,id',(ids[0],)).fetchall()
    assert len(rows)==5 and {r[1] for r in rows}=={actor.subject}
    assert {r[0] for r in rows}=={'membership_created','membership_role_changed','membership_suspended','membership_revoked','membership_active'}
    for query in ('DELETE FROM partner_memberships WHERE id=%s','UPDATE partner_events SET actor_subject=\'tampered\' WHERE membership_id=%s'):
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState), t['db'].transaction():
            t['db'].execute(query,(ids[0],))


def test_operator_cannot_provision_and_target_must_be_partner(identity_case):
    t = identity_case
    user = t['user']()
    with pytest.raises(HTTPException) as denied:
        memberships.set_membership(principal(t['user'](role='operator')),user['id'],t['flow']['partner'],'owner')
    assert denied.value.status_code==403
    with pytest.raises(HTTPException) as denied:
        memberships.set_membership(admin(t),uuid4(),t['flow']['partner'],'owner')
    assert denied.value.status_code==409


def test_admin_reads_global_but_tourist_cannot(identity_case):
    t = identity_case
    for role in ('admin','operator'):
        auth = headers(t['token'](t['user'](role=role)))
        assert t['client'].get(path(t),headers=auth).status_code==200
    auth = headers(t['token'](t['user'](role='tourist')))
    assert t['client'].get(path(t),headers=auth).status_code==403


def test_administrative_suspension_survives_paid_settlement(identity_case):
    t = identity_case
    actor = admin(t)
    _,pay,_ = commission(t['flow'])
    s = settlement(t['flow'])
    memberships.set_partner_state(actor,t['flow']['partner'],'suspended',True)
    user = t['user']()
    auth = headers(t['token'](user))
    assert t['client'].get(path(t),headers=auth).status_code == 200
    assert t['client'].post('/payments/'+pay['code']+'/confirm-partner',headers=auth).status_code == 403
    assert t['client'].post('/settlements/'+s['code']+'/report-payment',headers=auth,json={'payment_method':'cash'}).status_code == 200
    assert settlements.verify_settlement_payment(s['code'])['partner_reactivated'] is False
    assert t['db'].execute('SELECT status,suspension_source FROM partners WHERE id=%s',(t['flow']['partner'],)).fetchone()==('suspended','administrative')


def test_debt_suspension_can_be_cleared_by_settlement(identity_case):
    t = identity_case
    t['user']()  # Another effective owner; removal of the last is now forbidden.
    commission(t['flow'])
    s = settlement(t['flow'])
    t['db'].execute("UPDATE partners SET status='suspended',suspension_source='debt' WHERE id=%s",(t['flow']['partner'],))
    assert settle(s['code'])['partner_reactivated'] is True
    assert t['db'].execute('SELECT status,suspension_source FROM partners WHERE id=%s',(t['flow']['partner'],)).fetchone()==('active',None)


def test_membership_rechecked_after_dependency(identity_case, monkeypatch):
    from app import auth as auth_module
    t = identity_case
    user = t['user']()
    token = headers(t['token'](user))
    res = reserve(t['flow'])
    t['db'].execute("UPDATE partner_memberships SET status='revoked' WHERE user_id=%s",(user['id'],))
    monkeypatch.setattr(auth_module,'check_owner',lambda *args: None)
    assert t['client'].post('/reservations/'+res['code']+'/cancel',headers=token,json={'reason':'test'}).status_code==403


def test_reads_filter_actual_records_and_paginate(identity_case):
    t = identity_case
    own_res = reserve(t['flow'])
    other = partner(t)
    t['db'].execute('INSERT INTO product_partners(product_id,partner_id,partner_price) VALUES (%s,%s,100)', (t['flow']['product'],other))
    other_code = t['db'].execute('SELECT code FROM partners WHERE id=%s',(other,)).fetchone()[0]
    req = request(t['flow'])
    candidate = next(c for c in req['candidates'] if c['partner_code']==other_code)
    responses.respond_to_request(responses.PartnerResponseCreate(request_partner_id=candidate['request_partner_id'],action='accept'))
    other_res = reservations.create_reservation(reservations.ReservationCreate(service_request_code=req['code']))
    auth = headers(t['token'](t['user']()))
    result = t['client'].get(path(t,'/reservations'),headers=auth).json()['items']
    assert [r['code'] for r in result] == [own_res['code']]
    assert other_res['code'] not in {r['code'] for r in result}
    result = t['client'].get(path(t,'/requests'),headers=auth).json()['items']
    assert candidate['request_partner_id'] not in {r['request_partner_id'] for r in result}
    expected = {str(r[0]) for r in t['db'].execute('SELECT id FROM request_partners WHERE partner_id=%s',(t['flow']['partner'],)).fetchall()}
    assert {r['request_partner_id'] for r in result} == expected
    assert len(t['client'].get(path(t,'/products'),headers=auth).json()['items'])==1
    first = t['client'].get(path(t,'/requests')+'?limit=1&offset=0',headers=auth).json()['items']
    second = t['client'].get(path(t,'/requests')+'?limit=1&offset=1',headers=auth).json()['items']
    assert len(first)==len(second)==1 and first[0]['request_partner_id']!=second[0]['request_partner_id']
    assert t['client'].get(path(t,'/requests')+'?limit=101',headers=auth).status_code==422


def test_secondary_membership_authorizes_commercial_resource(identity_case):
    t = identity_case
    user = t['user']()
    other = partner(t)
    t['db'].execute('INSERT INTO product_partners(product_id,partner_id,partner_price) VALUES (%s,%s,100)', (t['flow']['product'],other))
    other_code = t['db'].execute('SELECT code FROM partners WHERE id=%s',(other,)).fetchone()[0]
    memberships.set_membership(admin(t),user['id'],other,'staff')
    req = request(t['flow'])
    candidate = next(c for c in req['candidates'] if c['partner_code']==other_code)
    auth = headers(t['token'](user))
    assert t['client'].post('/partner-responses',headers=auth,json={
        'request_partner_id':candidate['request_partner_id'],'action':'accept'}).status_code==200
    assert t['client'].post('/reservations',headers=auth,json={'service_request_code':req['code']}).status_code==201


def test_old_jwt_downgrade_restricts_financial_report(identity_case):
    t = identity_case
    t['user']()  # Another effective owner; removal of the last is now forbidden.
    user = t['user']()
    auth = headers(t['token'](user))
    commission(t['flow'])
    s = settlement(t['flow'])
    memberships.set_membership(admin(t),user['id'],t['flow']['partner'],'staff')
    assert t['client'].post('/settlements/'+s['code']+'/report-payment',headers=auth,json={'payment_method':'cash'}).status_code==403


def test_membership_constraints_and_idempotent_audit(identity_case):
    t = identity_case
    user = t['user'](membership=False)
    actor = admin(t)
    first = memberships.set_membership(actor,user['id'],t['flow']['partner'],'staff')
    assert memberships.set_membership(actor,user['id'],t['flow']['partner'],'staff')==first
    assert t['db'].execute('SELECT count(*) FROM partner_events WHERE membership_id=%s',(first['id'],)).fetchone()[0]==1
    with pytest.raises(psycopg.errors.UniqueViolation), t['db'].transaction():
        t['db'].execute("INSERT INTO partner_memberships(partner_id,user_id,membership_role) VALUES (%s,%s,'staff')",(t['flow']['partner'],user['id']))
    other = partner(t)
    with pytest.raises(psycopg.errors.CheckViolation), t['db'].transaction():
        t['db'].execute('UPDATE partner_memberships SET partner_id=%s WHERE id=%s',(other,first['id']))
    for role,status in [('operator','active'),('owner','deleted')]:
        with pytest.raises(HTTPException) as denied:
            memberships.set_membership(actor,user['id'],other,role,status)
        assert denied.value.status_code==422


def test_payment_partner_lock_precedes_payment_lock(identity_case, monkeypatch):
    """Prevent the partner→payment vs payment→partner deadlock with refunds."""
    from contextlib import contextmanager
    from app.routers import payments, passengers
    t = identity_case
    res = reserve(t['flow'])
    passengers.create_passengers(res['code'], passengers.PassengersCreate(passengers=[passenger()]))
    pay = payments.create_payment(payments.PaymentCreate(reservation_code=res['code'],payment_method='cash',received_by='partner'))
    auth = headers(t['token'](t['user']()))
    original = payments.get_connection
    statements = []
    class Cursor:
        def __init__(self, inner): self.inner = inner
        def __getattr__(self, key): return getattr(self.inner, key)
        def execute(self, sql, params=None):
            statements.append(' '.join(sql.lower().split()))
            return self.inner.execute(sql, params)
    class Connection:
        def __init__(self, inner): self.inner = inner
        def __getattr__(self, key): return getattr(self.inner, key)
        @contextmanager
        def cursor(self):
            with self.inner.cursor() as cur:
                yield Cursor(cur)
    @contextmanager
    def tracked_connection():
        with original() as conn:
            yield Connection(conn)
    monkeypatch.setattr(payments,'get_connection',tracked_connection)
    assert t['client'].post('/payments/'+pay['code']+'/confirm-partner',headers=auth).status_code==200
    partner_lock = next(i for i,q in enumerate(statements) if 'from partners' in q and 'for share' in q)
    payment_lock = next(i for i,q in enumerate(statements) if 'from payments' in q and 'for update' in q)
    reservation_lock = next(i for i,q in enumerate(statements) if 'from reservations' in q and 'for update' in q)
    assert partner_lock < reservation_lock < payment_lock
