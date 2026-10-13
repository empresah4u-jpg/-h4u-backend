"""Tourist reads under real JWT/restricted runtime in a disposable database."""
from uuid import uuid4
import pytest
import psycopg
from pydantic import ValidationError
from app.auth import Principal
from tests.test_commercial_e2e import e2e, request, offer, accept  # noqa: F401


def get(c, path='', owner='tourist'):
    return c['client'].get('/service-requests' + path, headers=c['headers'][owner])


def test_list_detail_ownership_and_pagination(e2e):
    c = e2e
    first = request(c)
    second = request(c)
    foreign = request(c, 'other')
    response = get(c, '?limit=1')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['has_more'] is True
    assert response.json()['items'][0]['id'] == second['id']
    page = get(c, '?limit=1&offset=1').json()
    assert page['has_more'] is False and page['items'][0]['id'] == first['id']
    detail = get(c, '/' + first['id']).json()
    assert set(detail) == {'id', 'code', 'product', 'destination', 'service_date', 'passenger_count', 'status', 'created_at', 'selected_partner', 'reservations'}
    assert detail['selected_partner'] is None and detail['reservations'] == []
    assert foreign['id'] not in get(c).text
    assert get(c, '/' + foreign['id']).status_code == 404
    assert get(c, '/' + str(uuid4())).status_code == 404
    for query in ('?traveler_id=' + str(c['users']['other']['traveler']), '?limit=101', '?offset=-1'):
        assert get(c, query).status_code == 422
    assert get(c, '/' + first['id'] + '?traveler_id=x').status_code == 422
    assert get(c, owner='A').status_code == 403


def test_real_states_partner_and_reservation_projection(e2e):
    c = e2e
    req = request(c)
    path = '/' + req['id']
    assert get(c, path).json()['status'] == 'searching'
    proposed = offer(c, req, 'A')
    assert get(c, path).json()['status'] == 'offers_received'
    assert accept(c, req, 'A', proposed['version']).status_code == 200
    selected = get(c, path).json()
    assert selected['status'] == 'partner_assigned'
    assert selected['selected_partner'] == {'name': 'TEST provider'}
    reservation = c['client'].post('/reservations', headers=c['headers']['tourist'], json={'service_request_code': req['code']})
    assert reservation.status_code == 201
    detail = get(c, path).json()
    assert detail['reservations'][0]['id'] == reservation.json()['id']
    assert set(detail['reservations'][0]) == {'id', 'code', 'status'}
    for status in ('created', 'awaiting_customer', 'confirmed', 'cancelled', 'expired'):
        c['db'].execute('UPDATE service_requests SET status=%s WHERE id=%s', (status, req['id']))
        assert get(c, path).json()['status'] == status


def test_empty_missing_identity_and_revocation(e2e):
    c = e2e
    assert get(c).json() == {'items': [], 'has_more': False}
    assert c['client'].get('/service-requests').status_code == 401
    assert c['client'].get('/service-requests', headers={'Authorization': 'Bearer expired.invalid.signature'}).status_code == 401
    c['db'].execute("UPDATE auth_sessions SET created_at=now()-interval '2 hours',expires_at=now()-interval '1 second' WHERE user_id=%s", (c['users']['other']['id'],))
    assert get(c, owner='other').status_code == 401
    # A tourist without a traveler is rejected by both DB and identity model.
    with pytest.raises(psycopg.errors.CheckViolation):
        c['db'].execute('UPDATE users SET traveler_id=NULL WHERE id=%s', (c['users']['tourist']['id'],))
    with pytest.raises(ValidationError):
        Principal(subject=str(c['users']['tourist']['id']), role='tourist', traveler_id=None)
    # Current DB identity, not a stale JWT, controls reads.
    c['db'].execute("UPDATE users SET status='disabled' WHERE id=%s", (c['users']['tourist']['id'],))
    assert get(c).status_code == 401
