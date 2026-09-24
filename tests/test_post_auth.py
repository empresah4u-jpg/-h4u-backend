"""HTTP/JWT integration against rollback fixtures, never real identities."""
from datetime import date, timedelta
from uuid import uuid4

import pytest

from app.routers import partner_responses as pr, passengers as ps, payments as pay
from tests.test_commercial import flow, request, reserve, passenger
from tests.test_identity_auth import identity_case, headers
from tests.test_financial_adjustments import commission, settlement, settle, refund


def bearer(case, role='partner', foreign=False):
    owner = None
    if foreign:
        if role == 'partner':
            owner = case['db'].execute("INSERT INTO partners(code,business_name,status,reservations_enabled) VALUES (%s,'test','active',true) RETURNING id", (uuid4().hex[:24],)).fetchone()[0]
        else:
            owner = case['db'].execute('INSERT INTO travelers DEFAULT VALUES RETURNING id').fetchone()[0]
    return headers(case['token'](case['user'](role=role, owner=owner)))


@pytest.mark.parametrize('action', ['accept', 'counter_offer'])
@pytest.mark.parametrize('disabled', ['partner_status', 'partner_reservations', 'link', 'product'])
def test_stale_candidate_revalidates_admission(identity_case, action, disabled):
    t = identity_case
    req = request(t['flow'])
    if disabled == 'partner_status':
        t['db'].execute("UPDATE partners SET status='suspended' WHERE id=%s", (t['flow']['partner'],))
    elif disabled == 'partner_reservations':
        t['db'].execute('UPDATE partners SET reservations_enabled=false WHERE id=%s', (t['flow']['partner'],))
    elif disabled == 'link':
        t['db'].execute("UPDATE product_partners SET status='inactive' WHERE partner_id=%s", (t['flow']['partner'],))
    else:
        t['db'].execute('UPDATE products SET reservations_enabled=false WHERE id=%s', (t['flow']['product'],))
    response = t['client'].post('/partner-responses', headers=bearer(t), json={
        'request_partner_id': req['candidates'][0]['request_partner_id'],
        'action': action, 'partner_message': 'test'})
    assert response.status_code == 409
    assert t['db'].execute('SELECT assigned_partner_id,status FROM service_requests WHERE code=%s', (req['code'],)).fetchone() == (None, 'searching')


@pytest.mark.parametrize('disabled', ['status', 'reservations', 'link'])
def test_reservation_revalidates_winning_partner(identity_case, disabled):
    t = identity_case
    req = request(t['flow'])
    pr.respond_to_request(pr.PartnerResponseCreate(request_partner_id=req['candidates'][0]['request_partner_id'], action='accept'))
    if disabled == 'status':
        t['db'].execute("UPDATE partners SET status='suspended' WHERE id=%s", (t['flow']['partner'],))
    elif disabled == 'reservations':
        t['db'].execute('UPDATE partners SET reservations_enabled=false WHERE id=%s', (t['flow']['partner'],))
    else:
        t['db'].execute("UPDATE product_partners SET status='inactive' WHERE partner_id=%s", (t['flow']['partner'],))
    response = t['client'].post('/reservations', headers=bearer(t, 'tourist'), json={'service_request_code': req['code']})
    assert response.status_code == 409
    assert t['db'].execute('SELECT count(*) FROM reservations WHERE service_request_id=%s', (req['id'],)).fetchone()[0] == 0


@pytest.mark.parametrize('operation', ['request', 'reserve', 'cancel', 'payment', 'confirm'])
def test_foreign_tourist_cannot_mutate(identity_case, operation):
    t = identity_case
    auth = bearer(t, 'tourist', foreign=True)
    if operation == 'request':
        path = '/service-requests'
        body = {'session_id': str(t['flow']['session']), 'product_id': str(t['flow']['product']),
                'service_date': str(date.today()+timedelta(days=7)), 'passenger_count': 1, 'adults_count': 1, 'minors_count': 0}
    elif operation == 'reserve':
        req = request(t['flow'])
        path, body = '/reservations', {'service_request_code': req['code']}
    else:
        res = reserve(t['flow'])
        if operation == 'cancel':
            path, body = '/reservations/'+res['code']+'/cancel', {'reason': 'test'}
        else:
            ps.create_passengers(res['code'], ps.PassengersCreate(passengers=[passenger()]))
            body = {'reservation_code': res['code'], 'payment_method': 'cash', 'received_by': 'partner'}
            path = '/payments'
            if operation == 'confirm':
                payment = pay.create_payment(pay.PaymentCreate(**body))
                path, body = '/payments/'+payment['code']+'/confirm-customer', {}
    assert t['client'].post(path, headers=auth, json=body).status_code == 403


@pytest.mark.parametrize('role', ['tourist', 'partner'])
@pytest.mark.parametrize('action', ['refund', 'commission', 'settlement_verify'])
def test_real_identity_cannot_execute_staff_finance(identity_case, role, action):
    t = identity_case
    _, payment, _ = commission(t['flow'])
    s = settlement(t['flow'])
    auth = bearer(t, role)
    path, body = {
        'refund': ('/refunds', {'payment_code': payment['code'], 'amount': 1, 'reason': 'test', 'idempotency_key': uuid4().hex}),
        'commission': ('/commissions/from-payment/'+payment['code'], {}),
        'settlement_verify': ('/settlements/'+s['code']+'/verify-payment', {}),
    }[action]
    assert t['client'].post(path, headers=auth, json=body).status_code == 403
    assert t['db'].execute('SELECT count(*) FROM refunds WHERE payment_id=%s', (payment['id'],)).fetchone()[0] == 0


@pytest.mark.parametrize('role', ['admin', 'operator'])
def test_staff_refund_records_real_actor_and_idempotency(identity_case, role):
    t = identity_case
    _, payment, _ = commission(t['flow'])
    user = t['user'](role=role)
    auth = headers(t['token'](user))
    body = {'payment_code': payment['code'], 'amount': 30, 'reason': 'test', 'idempotency_key': uuid4().hex}
    first = t['client'].post('/refunds', headers=auth, json=body)
    if role == 'operator':
        assert first.status_code == 403
        assert t['db'].execute('SELECT count(*) FROM refunds WHERE payment_id=%s',(payment['id'],)).fetchone()[0]==0
        return
    assert first.status_code == 201
    assert t['client'].post('/refunds', headers=auth, json=body).json() == first.json()
    assert t['db'].execute('SELECT actor_subject,actor_role FROM refunds WHERE id=%s', (first.json()['id'],)).fetchone() == (str(user['id']), role)
    assert t['db'].execute("SELECT actor_subject,actor_role FROM financial_events WHERE entity_id=%s AND event_type='processed'", (first.json()['id'],)).fetchone() == (str(user['id']), role)


@pytest.mark.parametrize('state', ['cancelled', 'refunded'])
def test_owner_cannot_confirm_invalid_payment_state(identity_case, state):
    t = identity_case
    if state == 'refunded':
        _, payment, _ = commission(t['flow'])
        refund(payment, 100)
    else:
        res = reserve(t['flow'])
        ps.create_passengers(res['code'], ps.PassengersCreate(passengers=[passenger()]))
        payment = pay.create_payment(pay.PaymentCreate(reservation_code=res['code'], payment_method='cash', received_by='partner'))
        from app.routers.reservations import cancel_reservation, ReservationCancel
        cancel_reservation(res['code'], ReservationCancel(reason='test'))
    assert t['client'].post('/payments/'+payment['code']+'/confirm-partner', headers=bearer(t)).status_code == 409


def test_paid_settlement_rejects_owner_report(identity_case):
    t = identity_case
    commission(t['flow'])
    s = settlement(t['flow'])
    settle(s['code'])
    assert t['client'].post('/settlements/'+s['code']+'/report-payment', headers=bearer(t), json={'payment_method': 'cash'}).status_code == 409


def test_revoked_staff_cannot_refund(identity_case):
    t = identity_case
    _, payment, _ = commission(t['flow'])
    auth = bearer(t, 'admin')
    assert t['client'].post('/auth/logout', headers=auth).status_code == 204
    assert t['client'].post('/refunds', headers=auth, json={'payment_code': payment['code'], 'amount': 1, 'reason': 'test'}).status_code == 401


@pytest.mark.parametrize('category,table', [('hotel', 'hotels'), ('restaurant', 'restaurants'), ('tour', 'tours'), ('attraction', 'attractions')])
def test_semantic_search_never_publishes_inactive_catalog_entries(flow, monkeypatch, category, table):
    from app.routers import semantic_search as search, reservations
    from app.embeddings import vector_to_pg, MODEL_NAME
    from psycopg import sql
    monkeypatch.setattr(search, 'get_connection', reservations.get_connection)
    vector = [1.0] + [0.0]*383
    monkeypatch.setattr(search, 'encode_query', lambda *args, **kwargs: vector)
    db = flow['conn']
    destination = db.execute('SELECT destination_id FROM products WHERE id=%s', (flow['product'],)).fetchone()[0]
    code = uuid4().hex[:16]
    entity = db.execute(sql.SQL('INSERT INTO {}(destination_id,code,name,status) VALUES (%s,%s,%s,\'active\') RETURNING id').format(sql.Identifier(table)), (destination,code,code)).fetchone()[0]
    db.execute('''INSERT INTO entity_embeddings(destination_id,entity_type,entity_id,content,embedding,embedding_model)
        VALUES (%s,%s,%s,%s,%s::vector,%s)''', (destination,category,entity,code,vector_to_pg(vector),MODEL_NAME))
    assert code in {r['code'] for r in search.semantic_search(q=code,category=category,limit=50)['results']}
    db.execute(sql.SQL("UPDATE {} SET status='inactive' WHERE id=%s").format(sql.Identifier(table)), (entity,))
    assert code not in {r['code'] for r in search.semantic_search(q=code,category=category,limit=50)['results']}


@pytest.mark.parametrize('operation', ['reserve', 'passengers', 'payment'])
def test_foreign_partner_cannot_mutate_other_business(identity_case, operation):
    t = identity_case
    auth = bearer(t, foreign=True)
    if operation == 'reserve':
        req = request(t['flow'])
        pr.respond_to_request(pr.PartnerResponseCreate(request_partner_id=req['candidates'][0]['request_partner_id'], action='accept'))
        path, body = '/reservations', {'service_request_code': req['code']}
    else:
        res = reserve(t['flow'])
        if operation == 'passengers':
            path, body = '/reservations/'+res['code']+'/passengers', {'passengers': [passenger().model_dump(mode='json')]}
        else:
            ps.create_passengers(res['code'], ps.PassengersCreate(passengers=[passenger()]))
            path, body = '/payments', {'reservation_code': res['code'], 'payment_method': 'cash', 'received_by': 'partner'}
    assert t['client'].post(path, headers=auth, json=body).status_code == 403


def test_suspended_partner_can_reject_and_report_existing_debt(identity_case):
    t = identity_case
    commission(t['flow'])
    s = settlement(t['flow'])
    req = request(t['flow'])
    t['db'].execute("UPDATE partners SET status='suspended' WHERE id=%s", (t['flow']['partner'],))
    auth = bearer(t)
    assert t['client'].post('/partner-responses', headers=auth, json={
        'request_partner_id': req['candidates'][0]['request_partner_id'], 'action': 'reject'}).status_code == 200
    assert t['client'].post('/settlements/'+s['code']+'/report-payment', headers=auth, json={'payment_method': 'cash'}).status_code == 200
