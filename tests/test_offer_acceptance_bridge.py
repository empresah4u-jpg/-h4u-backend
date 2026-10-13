"""Existing acceptance engine, real JWT, disposable PostgreSQL only."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
import pytest
import psycopg
from tests.test_commercial_e2e import e2e, request, offer, accept  # noqa: F401


def counts(c, req):
    return c['db'].execute('''SELECT
        (SELECT count(*) FROM request_offer_consents WHERE service_request_id=%s),
        (SELECT count(*) FROM request_partners WHERE service_request_id=%s AND is_winner),
        (SELECT count(*) FROM reservations WHERE service_request_id=%s),
        (SELECT count(*) FROM payments)''',(req['id'],req['id'],req['id'])).fetchone()


def test_accept_snapshot_replay_no_reservation_or_payment(e2e):
    c=e2e;req=request(c);v=offer(c,req,'A')['version'];before=counts(c,req)
    response=accept(c,req,'A',v);assert response.status_code==200
    replay=accept(c,req,'A',v);assert replay.status_code==200 and replay.json()['replayed']
    assert response.json()['consent_id']==replay.json()['consent_id']
    assert counts(c,req)==(1,1,before[2],before[3])
    offers=c['client'].get('/service-requests/'+req['id']+'/offers',headers=c['headers']['tourist']).json()['items']
    selected=next(o for o in offers if o['id']==req['candidate']['A'])
    assert selected['status']=='accepted' and selected['flexible_time'] is False
    assert selected['adults_count']==2 and selected['minors_count']==0
    assert selected['request_notes']=='TEST: two adults, no transfer'
    for sql in ('UPDATE request_offer_consents SET amount_total=1 WHERE service_request_id=%s', 'DELETE FROM request_offer_consents WHERE service_request_id=%s'):
        with pytest.raises(psycopg.errors.CheckViolation):c['db'].execute(sql,(req['id'],))


@pytest.mark.parametrize('field,value',[('proposed_price','91'),('proposed_time','12:00'),('partner_message','TEST changed terms')])
def test_material_offer_changes_require_new_review(e2e,field,value):
    c=e2e;req=request(c);v=offer(c,req,'A')['version']
    # Fixed SQL identifiers from parametrization, synthetic fixture only.
    from psycopg import sql
    c['db'].execute(sql.SQL('UPDATE request_partners SET {}=%s WHERE id=%s').format(sql.Identifier(field)),(value,req['candidate']['A']))
    assert accept(c,req,'A',v).status_code==409
    assert counts(c,req)[:2]==(0,0)


def test_owner_revision_expiry_closed_and_validation(e2e):
    c=e2e;req=request(c);v=offer(c,req,'A')['version']
    assert accept(c,req,'A',v,'other').status_code in (403,404)
    assert accept(c,req,'A',v+1).status_code==409
    assert accept(c,req,'A',v,price=1).status_code==422
    path='/partner-responses/'+req['candidate']['A']+'/accept-counter-offer'
    assert c['client'].post(path,json={'expected_version':v}).status_code==401
    assert c['client'].post('/partner-responses/'+str(uuid4())+'/accept-counter-offer',json={'expected_version':v},headers=c['headers']['tourist']).status_code==403  # Existing ownership dependency hides absent candidates.
    c['db'].execute("UPDATE service_requests SET additional_notes='TEST revised' WHERE id=%s",(req['id'],))
    assert accept(c,req,'A',v).status_code==409
    v=offer(c,req,'A')['version']
    c['db'].execute("UPDATE request_partners SET expires_at=now()-interval '1 second' WHERE id=%s",(req['candidate']['A'],))
    assert accept(c,req,'A',v).status_code==409
    c['db'].execute("UPDATE service_requests SET status='cancelled' WHERE id=%s",(req['id'],))
    assert accept(c,req,'A',v).status_code==409
    assert counts(c,req)[:2]==(0,0)


@pytest.mark.parametrize('same',[True,False])
def test_two_tabs_one_winner(e2e,same):
    c=e2e;req=request(c);versions={n:offer(c,req,n)['version'] for n in ('A','B')};barrier=Barrier(2)
    def attempt(name):
        barrier.wait();return accept(c,req,name,versions[name])
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(attempt,('A','A' if same else 'B')))
    assert sorted(r.status_code for r in results)==([200,200] if same else [200,409])
    assert counts(c,req)[:3]==(1,1,0)


def test_atomic_rollback(e2e,monkeypatch):
    from app.routers import partner_responses as pr
    from fastapi import HTTPException
    c=e2e;req=request(c);v=offer(c,req,'A')['version'];original=pr.record_consent
    def fail_after_insert(*args):
        original(*args);raise HTTPException(503,'Synthetic transaction failure')
    monkeypatch.setattr(pr,'record_consent',fail_after_insert)
    assert accept(c,req,'A',v).status_code==503
    assert counts(c,req)[:3]==(0,0,0)
    monkeypatch.setattr(pr,'record_consent',original)
    assert accept(c,req,'A',v).status_code==200
