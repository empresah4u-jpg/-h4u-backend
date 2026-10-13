"""Read-only public offers with synthetic candidates; never accept/create consent."""
from uuid import uuid4
from tests.test_commercial_e2e import e2e, request, offer  # noqa: F401


def test_offers_ownership_versions_and_visibility(e2e):
    c = e2e
    req = request(c)
    path = '/service-requests/' + req['id'] + '/offers'
    get = lambda owner='tourist': c['client'].get(path, headers=c['headers'][owner])
    assert get().json() == {'items': []}
    assert get('other').status_code == 404
    assert get('A').status_code == 403
    assert c['client'].get(path).status_code == 401
    assert c['client'].get('/service-requests/' + str(uuid4()) + '/offers', headers=c['headers']['tourist']).status_code == 404
    assert c['client'].get(path+'?traveler_id=x',headers=c['headers']['tourist']).status_code == 422
    offer(c, req, 'A')
    offer(c, req, 'B')
    first = get()
    assert first.status_code == 200 and first.headers['cache-control'] == 'no-store'
    items = first.json()['items']
    assert len(items) == 2 and {i['status'] for i in items} == {'current'}
    assert set(items[0]) == {'id','provider','amount_total','currency','service_date','service_time','passenger_count','conditions','offer_version','request_revision','expires_at','status','flexible_time','adults_count','minors_count','request_notes'}
    old = next(i for i in items if i['id'] == req['candidate']['A'])
    offer(c, req, 'A')
    new = get().json()['items']
    assert len(new) == 2  # replaced content is not a fabricated historical offer
    assert next(i for i in new if i['id']==old['id'])['offer_version'] > old['offer_version']
    c['db'].execute('UPDATE service_requests SET passenger_count=3,adults_count=3 WHERE id=%s',(req['id'],))
    stale = get().json()['items']
    assert {i['status'] for i in stale} == {'request_changed'}
    assert {i['passenger_count'] for i in stale} == {old['passenger_count']}
    c['db'].execute("UPDATE service_requests SET status='cancelled' WHERE id=%s",(req['id'],))
    assert {i['status'] for i in get().json()['items']} == {'request_closed'}


def test_missing_price_expiry_and_revoked_session(e2e):
    c=e2e; req=request(c)
    path='/service-requests/'+req['id']+'/offers'
    offer(c,req,'A')
    c['db'].execute('UPDATE request_partners SET proposed_price=NULL WHERE id=%s',(req['candidate']['A'],))
    get=lambda:c['client'].get(path,headers=c['headers']['tourist'])
    assert get().json()['items'][0]['status']=='unknown'
    assert get().json()['items'][0]['amount_total'] is None
    c['db'].execute("UPDATE request_partners SET expires_at=now()-interval '1 second' WHERE id=%s",(req['candidate']['A'],))
    assert get().json()['items'][0]['status']=='expired'
    c['db'].execute("UPDATE auth_sessions SET created_at=now()-interval '2 hours',expires_at=now()-interval '1 second' WHERE user_id=%s",(c['users']['tourist']['id'],))
    assert get().status_code==401


def test_ineligible_and_obsolete_are_not_current(e2e):
    c=e2e; req=request(c)
    offer(c,req,'A')
    path='/service-requests/'+req['id']+'/offers'
    get=lambda:c['client'].get(path,headers=c['headers']['tourist']).json()['items'][0]
    c['db'].execute('UPDATE products SET reservations_enabled=false WHERE id=%s',(c['product'],))
    assert get()['status']=='unavailable'
    c['db'].execute("UPDATE request_partners SET status='rejected' WHERE id=%s",(req['candidate']['A'],))
    assert get()['status']=='obsolete'
