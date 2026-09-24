from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import auth
from app.main import app
from app.routers import reservations as r
from tests.test_commercial import flow, request, reserve, passenger, payment
from tests.test_identity_auth import identity_case


@contextmanager
def as_actor(actor):
    old = app.dependency_overrides.copy()
    app.dependency_overrides[auth.get_current_actor] = lambda: actor
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old)


@pytest.mark.parametrize('path', [
    '/service-requests','/partner-responses','/reservations',
    '/reservations/RES-X/cancel','/reservations/RES-X/passengers',
    '/payments','/payments/PAY-X/confirm-partner','/payments/PAY-X/confirm-customer',
    '/refunds','/commissions/from-payment/PAY-X','/settlements',
    '/settlements/SET-X/report-payment','/settlements/SET-X/verify-payment',
    '/settlements/process-overdue',
])
def test_all_commercial_operations_require_identity(path):
    response=TestClient(app).post(path,json={},headers={'X-Role':'admin','X-Partner-Id':str(uuid4())})
    assert response.status_code==401
    assert response.headers['www-authenticate']=='Bearer'


@pytest.mark.parametrize('path', ['/refunds','/commissions/from-payment/PAY-X','/settlements','/settlements/SET-X/verify-payment','/settlements/process-overdue'])
@pytest.mark.parametrize('role',['tourist','partner'])
def test_financial_admin_operations_reject_other_roles(path,role):
    with as_actor(auth.Principal(subject='test',role=role,partner_id=uuid4(),traveler_id=uuid4())) as client:
        assert client.post(path,json={}).status_code==403


@pytest.fixture
def ownership(identity_case):
    flow = identity_case['flow']
    flow['partner_user'] = identity_case['user']()['id']
    return flow


def test_partner_cannot_cancel_other_reservation(ownership):
    res=reserve(ownership)
    with as_actor(auth.Principal(subject='other-partner',role='partner',partner_id=uuid4())) as client:
        assert client.post('/reservations/'+res['code']+'/cancel',json={'reason':'foreign'}).status_code==403
    assert ownership['conn'].execute('SELECT status FROM reservations WHERE code=%s',(res['code'],)).fetchone()[0]=='awaiting_passenger_data'
    with as_actor(auth.Principal(subject=str(ownership['partner_user']),role='partner',partner_id=ownership['partner'])) as client:
        assert client.post('/reservations/'+res['code']+'/cancel',json={'reason':'own'}).status_code==200


def test_partner_response_ownership(ownership):
    req=request(ownership)
    body={'request_partner_id':req['candidates'][0]['request_partner_id'],'action':'accept'}
    with as_actor(auth.Principal(subject='other',role='partner',partner_id=uuid4())) as client:
        assert client.post('/partner-responses',json=body).status_code==403
    with as_actor(auth.Principal(subject=str(ownership['partner_user']),role='partner',partner_id=ownership['partner'])) as client:
        assert client.post('/partner-responses',json=body).status_code==200


def test_tourist_cannot_access_foreign_passengers(ownership):
    res=reserve(ownership)
    data={'passengers':[passenger().model_dump(mode='json')]}
    with as_actor(auth.Principal(subject='other',role='tourist',traveler_id=uuid4())) as client:
        assert client.post('/reservations/'+res['code']+'/passengers',json=data).status_code==403
    traveler=ownership['conn'].execute('SELECT traveler_id FROM sessions WHERE id=%s',(ownership['session'],)).fetchone()[0]
    with as_actor(auth.Principal(subject='owner',role='tourist',traveler_id=traveler)) as client:
        assert client.post('/reservations/'+res['code']+'/passengers',json=data).status_code==201


def test_simulation_is_staff_only():
    with as_actor(auth.Principal(subject='tourist',role='tourist',traveler_id=uuid4())) as client:
        assert client.post('/service-requests',json={'simulation':True}).status_code==403


def test_partner_cannot_confirm_as_customer():
    with as_actor(auth.Principal(subject='partner',role='partner',partner_id=uuid4())) as client:
        assert client.post('/payments/X/confirm-customer').status_code==403


def test_missing_owner_identity_rejected():
    with pytest.raises(ValidationError):
        auth.Principal(subject='bad',role='partner')


def test_bearer_without_identity_provider_rejected():
    assert TestClient(app).post('/refunds',json={},headers={'Authorization':'Bearer unsigned-admin'}).status_code==401


def test_verified_provider_adapter(monkeypatch):
    class Provider:
        async def authenticate(self,token):
            if token=='valid-test-token':
                return auth.Principal(subject='admin-from-provider',role='admin')
            return None
    monkeypatch.setattr(app.state,'identity_provider',Provider(),raising=False)
    client=TestClient(app)
    assert client.post('/refunds',json={},headers={'Authorization':'Bearer invalid'}).status_code==401
    # Authenticated staff reaches body validation (not an auth bypass).
    assert client.post('/refunds',json={},headers={'Authorization':'Bearer valid-test-token'}).status_code==422


def test_openapi_declares_bearer():
    schema=app.openapi()
    assert schema['paths']['/refunds']['post']['security']==[{'HTTPBearer':[]}]
    assert 'security' not in schema['paths']['/hotels']['get']


def test_partner_cannot_confirm_foreign_payment(ownership):
    _,pay=payment(ownership)
    with as_actor(auth.Principal(subject='foreign',role='partner',partner_id=uuid4())) as client:
        assert client.post('/payments/'+pay['code']+'/confirm-partner').status_code==403


def test_partner_cannot_report_foreign_settlement(ownership):
    from tests.test_financial_adjustments import commission, settlement
    commission(ownership)
    s=settlement(ownership)
    with as_actor(auth.Principal(subject='foreign',role='partner',partner_id=uuid4())) as client:
        assert client.post('/settlements/'+s['code']+'/report-payment',json={'payment_method':'cash'}).status_code==403
    with as_actor(auth.Principal(subject=str(ownership['partner_user']),role='partner',partner_id=ownership['partner'])) as client:
        assert client.post('/settlements/'+s['code']+'/report-payment',json={'payment_method':'cash'}).status_code==200


def test_ownership_rechecked_inside_business_transaction(ownership,monkeypatch):
    res=reserve(ownership)
    # A stale preliminary ownership decision must not authorize the mutation.
    monkeypatch.setattr(auth,'check_owner',lambda *args: None)
    with as_actor(auth.Principal(subject='foreign',role='partner',partner_id=uuid4())) as client:
        assert client.post('/reservations/'+res['code']+'/cancel',json={'reason':'stale-owner'}).status_code==403
    assert ownership['conn'].execute('SELECT status FROM reservations WHERE code=%s',(res['code'],)).fetchone()[0]=='awaiting_passenger_data'
