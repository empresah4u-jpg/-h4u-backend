from datetime import date, timedelta, time
from decimal import Decimal

import pytest
from fastapi import HTTPException
from app.routers import lifecycle as api, reservations
from app.services import commercial_lifecycle as service
from app.services.cancellation_policy import CancellationPolicy
from tests.test_commercial import flow, reserve, request, payment, conflict


@pytest.fixture
def lifecycle(flow,monkeypatch):
    monkeypatch.setattr(api,'get_connection',reservations.get_connection)
    return flow


def configured(flow,refund='100'):
    # A test identity context; policy rules must explicitly allow the actual actor.
    from app.auth import _current_actor, Principal
    token=_current_actor.set(Principal(subject='policy-test-admin',role='admin'))
    p=api.create_policy(api.PolicyCreate(name='test',policy=CancellationPolicy(rules=[dict(
        actors=['admin'],minimum_notice_seconds=0,outcome='cancel',refund_type='percentage',refund_value=refund)])))
    api.assign_policy(api.PolicyAssignment(product_id=flow['product'],policy_id=p['id']))
    return token


def with_time(flow,res):
    flow['conn'].execute("UPDATE reservations SET service_at=clock_timestamp()+interval '1 day' WHERE id=%s",(res['id'],))


def test_missing_policy_persists_review_and_replay(lifecycle):
    res=reserve(lifecycle)
    payload=api.CancellationRequest(reason='test',idempotency_key='review')
    result=api.request_cancellation(res['code'],payload)
    assert result['status']=='requires_manual_review'
    assert api.request_cancellation(res['code'],payload)==result
    conflict(api.request_cancellation,res['code'],api.CancellationRequest(reason='changed',idempotency_key='review'))
    assert lifecycle['conn'].execute('SELECT status FROM reservations WHERE id=%s',(res['id'],)).fetchone()[0]=='awaiting_passenger_data'


def test_paid_cancellation_creates_command_not_false_refund(lifecycle):
    res,pay=payment(lifecycle); with_time(lifecycle,res)
    from app.auth import _current_actor
    token=configured(lifecycle)
    try:
        result=api.request_cancellation(res['code'],api.CancellationRequest(reason='cancel',idempotency_key='paid'))
        assert result['status']=='cancelled' and result['refund_execution']=='pending'
        assert lifecycle['conn'].execute('SELECT status FROM payments WHERE code=%s',(pay['code'],)).fetchone()[0]=='paid'
        assert lifecycle['conn'].execute('SELECT count(*) FROM refunds WHERE payment_id=%s',(pay['id'],)).fetchone()[0]==0
    finally: _current_actor.reset(token)


def test_cancel_request_idempotent_and_reservation_blocks(lifecycle):
    req=request(lifecycle)
    assert api.cancel_request(req['code'],api.Reason(reason='test'))['status']=='cancelled'
    assert api.cancel_request(req['code'],api.Reason(reason='test'))['already_applied']
    res=reserve(lifecycle)
    conflict(api.cancel_request,res['service_request'],api.Reason(reason='test'))


@pytest.mark.parametrize('method,target',[(api.no_show,'no_show'),(api.complete,'completed')])
def test_service_outcome_requires_time_and_evidence(lifecycle,method,target):
    res,_=payment(lifecycle)
    conflict(method,res['code'],api.Reason(reason='evidence'))
    lifecycle['conn'].execute("UPDATE reservations SET service_at=clock_timestamp()-interval '1 hour' WHERE id=%s",(res['id'],))
    assert method(res['code'],api.Reason(reason='observed'))['status']==target
    assert method(res['code'],api.Reason(reason='observed'))['already_applied']
    other=api.complete if target=='no_show' else api.no_show
    conflict(other,res['code'],api.Reason(reason='invalid'))


def test_slot_rejects_missing_time_and_full_capacity(lifecycle):
    api.configure_product(lifecycle['product'],api.Settings(scheduled_only=True))
    conflict(reserve,lifecycle)
    slot=api.configure_slot(api.SlotCreate(product_id=lifecycle['product'],partner_id=lifecycle['partner'],
        service_date=date.today()+timedelta(days=7),service_time=time(10),capacity=1))
    with lifecycle['conn'].cursor() as cur:
        assert service.slot_for_reservation(cur,lifecycle['product'],lifecycle['partner'],date.today()+timedelta(days=7),time(10),1)==__import__('uuid').UUID(slot['id'])
        conflict(service.slot_for_reservation,cur,lifecycle['product'],lifecycle['partner'],date.today()+timedelta(days=7),time(10),2)


def test_policy_version_is_immutable(lifecycle):
    from app.auth import _current_actor
    import psycopg
    token=configured(lifecycle)
    try:
        with pytest.raises(psycopg.Error), lifecycle['conn'].transaction():
            lifecycle['conn'].execute("UPDATE cancellation_policy_versions SET name='tampered'")
    finally: _current_actor.reset(token)


def test_refund_command_confirmation_is_idempotent(lifecycle):
    from app.auth import _current_actor
    from app.routers import refunds
    res,pay=payment(lifecycle); with_time(lifecycle,res)
    token=configured(lifecycle,'50')
    try:
        case=api.request_cancellation(res['code'],api.CancellationRequest(reason='test',idempotency_key='execute'))
        conflict(refunds.create_refund,refunds.RefundCreate(payment_code=pay['code'],amount=Decimal('60'),reason='other',idempotency_key='other'))
        payload=api.RefundConfirmation(external_reference='TEST-CONFIRMED-TRANSFER')
        first=api.confirm_refund_command(case['refund_command_id'],payload)
        assert first['refund_amount']==50
        assert api.confirm_refund_command(case['refund_command_id'],payload)==first
        conflict(api.confirm_refund_command,case['refund_command_id'],api.RefundConfirmation(external_reference='different'))
    finally: _current_actor.reset(token)


def test_expirations_are_idempotent_and_preserve_paid(lifecycle):
    from app.services.expirations import expire_request,expire_reservation
    req=request(lifecycle)
    db=lifecycle['conn']
    db.execute("UPDATE service_requests SET expires_at=clock_timestamp()-interval '1 second' WHERE code=%s",(req['code'],))
    with db.cursor() as cur:
        assert expire_request(cur,req['code'])
        assert not expire_request(cur,req['code'])
    res=reserve(lifecycle)
    db.execute("UPDATE reservations SET expires_at=clock_timestamp()-interval '1 second' WHERE code=%s",(res['code'],))
    with db.cursor() as cur:
        assert expire_reservation(cur,res['code'])
        assert not expire_reservation(cur,res['code'])
    paid,_=payment(lifecycle)
    db.execute("UPDATE reservations SET expires_at=clock_timestamp()-interval '1 second' WHERE code=%s",(paid['code'],))
    with db.cursor() as cur: assert not expire_reservation(cur,paid['code'])


def test_fake_refund_provider_rejects_normal_database(lifecycle):
    from app.services.refund_execution import confirm
    from uuid import uuid4
    with lifecycle['conn'].cursor() as cur:
        conflict(confirm,cur,uuid4(),'fake','demo_fake',status=403)


def test_manual_resolution_preserves_original_case(lifecycle):
    from app.auth import _current_actor,Principal
    res=reserve(lifecycle); with_time(lifecycle,res)
    original=api.request_cancellation(res['code'],api.CancellationRequest(reason='request',idempotency_key='original'))
    token=_current_actor.set(Principal(subject='resolver',role='admin'))
    try:
        policy=api.create_policy(api.PolicyCreate(name='explicit review decision',policy=CancellationPolicy(rules=[dict(
            actors=['admin'],minimum_notice_seconds=0,outcome='cancel',refund_type='none',refund_value='0')])))
        body=api.ReviewResolution(reason='approved evidence',idempotency_key='resolution',policy_id=policy['id'])
        result=api.resolve_cancellation(original['cancellation_id'],body)
        assert result['status']=='cancelled' and result['resolves_case_id']==original['cancellation_id']
        assert api.resolve_cancellation(original['cancellation_id'],body)==result
        assert api.cancellation_case(original['cancellation_id'])['result']==original
    finally: _current_actor.reset(token)


def test_expired_candidate_is_not_accepted_before_worker(lifecycle):
    from app.routers import partner_responses
    req=request(lifecycle)
    identifier=req['candidates'][0]['request_partner_id']
    lifecycle['conn'].execute("UPDATE request_partners SET expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",(identifier,))
    conflict(partner_responses.respond_to_request,partner_responses.PartnerResponseCreate(request_partner_id=identifier,action='accept'))


def test_cancelled_cannot_complete(lifecycle):
    from tests.test_commercial import approved_cancellation
    res=reserve(lifecycle)
    approved_cancellation(lifecycle,res)
    conflict(api.complete,res['code'],api.Reason(reason='invalid completion'))


def test_slot_release_on_expiration_and_outbox_idempotence(lifecycle):
    from app.routers import service_requests as sr,partner_responses as pr
    from app.services.expirations import expire_reservation
    f=lifecycle
    slot=api.configure_slot(api.SlotCreate(product_id=f['product'],partner_id=f['partner'],service_date=date.today()+timedelta(days=7),service_time=time(10),capacity=2))
    req=sr.create_service_request(sr.ServiceRequestCreate(session_id=f['session'],product_id=f['product'],service_date=date.today()+timedelta(days=7),preferred_time=time(10),passenger_count=2,adults_count=2,minors_count=0))
    pr.respond_to_request(pr.PartnerResponseCreate(request_partner_id=req['candidates'][0]['request_partner_id'],action='accept'))
    res=reservations.create_reservation(reservations.ReservationCreate(service_request_code=req['code']))
    db=f['conn']; assert db.execute('SELECT reserved FROM commercial_slots WHERE id=%s',(slot['id'],)).fetchone()==(2,)
    db.execute("UPDATE reservations SET expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",(res['id'],))
    with db.cursor() as cur:
        assert expire_reservation(cur,res['code'])
        assert not expire_reservation(cur,res['code'])
    assert db.execute('SELECT reserved FROM commercial_slots WHERE id=%s',(slot['id'],)).fetchone()==(0,)
    assert db.execute("SELECT count(*) FROM notification_events WHERE kind='reservation.expired' AND reservation_id=%s",(res['id'],)).fetchone()==(1,)


def test_explicit_inventory_cannot_be_bypassed_by_omitting_time(lifecycle):
    f=lifecycle
    api.configure_slot(api.SlotCreate(product_id=f['product'],partner_id=f['partner'],service_date=date.today()+timedelta(days=7),service_time=time(10),capacity=1))
    # No scheduled_only setting: the existence of explicit inventory is sufficient.
    conflict(reserve,f)
    with f['conn'].cursor() as cur:
        conflict(service.slot_for_reservation,cur,f['product'],f['partner'],date.today()+timedelta(days=7),time(11),1)
