"""Real concurrent connections, persistent fictional fixtures ONLY in isolated demo.

No deletion/cleanup of financial history. No production credentials or identities.
"""
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, timedelta, time
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from app.demo.safety import configure, verify
from app.db import get_connection
from app.routers import service_requests as sr, partner_responses as pr, reservations as r
from app.routers import passengers as ps, payments as p, commissions as c, settlements as st, refunds as rf
from app.routers import lifecycle as api
from tests.test_commercial import request, reserve, passenger, payment


@pytest.fixture
def demo_flow(monkeypatch):
    with patch.dict(os.environ,clear=False):
        configure()
        token=uuid4().hex[:12]
        with get_connection() as conn:
            verify(conn)
            destination=conn.execute("INSERT INTO destinations(code,name,slug) VALUES (%s,'Concurrency DEMO',%s) RETURNING id",('RACE-'+token,'race-'+token)).fetchone()[0]
            traveler=conn.execute('INSERT INTO travelers DEFAULT VALUES RETURNING id').fetchone()[0]
            session=conn.execute("INSERT INTO sessions(traveler_id,destination_id,channel) VALUES (%s,%s,'demo') RETURNING id",(traveler,destination)).fetchone()[0]
            product=conn.execute("INSERT INTO products(destination_id,code,name,slug,product_type,status,reservations_enabled,requires_payment) VALUES (%s,%s,'Concurrency DEMO',%s,'tour','active',true,true) RETURNING id",(destination,'RACE-'+token,'race-'+token)).fetchone()[0]
            partner=conn.execute("INSERT INTO partners(code,business_name,status,reservations_enabled,commission_value) VALUES (%s,'Concurrency DEMO','active',true,10) RETURNING id",('RACE-'+token,)).fetchone()[0]
            conn.execute('INSERT INTO product_partners(product_id,partner_id,partner_price) VALUES (%s,%s,100)',(product,partner))
        connections=set()
        @contextmanager
        def connection():
            with get_connection() as conn:
                verify(conn)
                conn.execute("SET lock_timeout='5s'")
                conn.execute("SET statement_timeout='10s'")
                connections.add(conn.info.backend_pid)
                conn.commit()
                yield conn
        for module in (sr,pr,r,ps,p,c,st,rf,api): monkeypatch.setattr(module,'get_connection',connection)
        yield dict(session=session,product=product,partner=partner,partner_code='RACE-'+token,connections=connections)


def pair(first,second):
    barrier=Barrier(2)
    def invoke(fn):
        barrier.wait(timeout=10)
        try: return ('ok',fn())
        except HTTPException as exc: return ('http',exc.status_code)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(invoke,fn) for fn in (first,second)]
        return [f.result(timeout=30) for f in futures]


def settlement(flow):
    return st.create_settlement(st.SettlementCreate(partner_code=flow['partner_code'],
        period_start=date.today(),period_end=date.today(),due_date=date.today(),currency='PEN'))


def test_last_capacity_has_exactly_one_winner(demo_flow):
    f=demo_flow
    api.configure_slot(api.SlotCreate(product_id=f['product'],partner_id=f['partner'],service_date=date.today()+timedelta(days=7),service_time=time(10),capacity=1))
    codes=[]
    for _ in range(2):
        req=sr.create_service_request(sr.ServiceRequestCreate(session_id=f['session'],product_id=f['product'],service_date=date.today()+timedelta(days=7),preferred_time=time(10),passenger_count=1,adults_count=1,minors_count=0))
        pr.respond_to_request(pr.PartnerResponseCreate(request_partner_id=req['candidates'][0]['request_partner_id'],action='accept'))
        codes.append(req['code'])
    before=set(f['connections'])
    result=pair(lambda:r.create_reservation(r.ReservationCreate(service_request_code=codes[0])),lambda:r.create_reservation(r.ReservationCreate(service_request_code=codes[1])))
    assert sorted(x[0] for x in result)==['http','ok']
    assert ('http',409) in result
    assert len(f['connections']-before)==2
    with get_connection() as conn:
        assert conn.execute('SELECT reserved,capacity FROM commercial_slots WHERE product_id=%s',(f['product'],)).fetchone()==(1,1)


def test_duplicate_reservation_and_payment_race(demo_flow):
    f=demo_flow; req=request(f)
    pr.respond_to_request(pr.PartnerResponseCreate(request_partner_id=req['candidates'][0]['request_partner_id'],action='accept'))
    create=lambda:r.create_reservation(r.ReservationCreate(service_request_code=req['code']))
    results=pair(create,create)
    assert sum(x[0]=='ok' for x in results)==1
    res=next(x[1] for x in results if x[0]=='ok')
    ps.create_passengers(res['code'],ps.PassengersCreate(passengers=[passenger()]))
    create=lambda:p.create_payment(p.PaymentCreate(reservation_code=res['code'],payment_method='cash',received_by='partner'))
    results=pair(create,create)
    assert sum(x[0]=='ok' for x in results)==1


def test_simultaneous_confirmations(demo_flow):
    res=reserve(demo_flow)
    ps.create_passengers(res['code'],ps.PassengersCreate(passengers=[passenger()]))
    pay=p.create_payment(p.PaymentCreate(reservation_code=res['code'],payment_method='cash',received_by='partner'))
    results=pair(lambda:p.confirm_cash_by_partner(pay['code']),lambda:p.confirm_cash_by_customer(pay['code']))
    assert all(x[0]=='ok' for x in results)
    with get_connection() as conn:
        assert conn.execute('SELECT status FROM payments WHERE id=%s',(pay['id'],)).fetchone()==('paid',)
        assert conn.execute("SELECT count(*) FROM notification_events WHERE kind='payment.confirmed' AND reservation_id=%s",(res['id'],)).fetchone()==(1,)


def test_two_refunds_cannot_exceed_payment(demo_flow):
    _,pay=payment(demo_flow)
    results=pair(*[lambda key=key:rf.create_refund(rf.RefundCreate(payment_code=pay['code'],amount=60,reason='Race DEMO',idempotency_key=key)) for key in ('one','two')])
    assert sum(x[0]=='ok' for x in results)==1 and ('http',409) in results
    with get_connection() as conn:
        assert conn.execute("SELECT sum(amount) FROM refunds WHERE payment_id=%s AND status='processed'",(pay['id'],)).fetchone()[0]==60


def test_duplicate_commission_and_settlement(demo_flow):
    _,pay=payment(demo_flow)
    results=pair(lambda:c.create_commission_from_payment(pay['code']),lambda:c.create_commission_from_payment(pay['code']))
    assert all(x[0]=='ok' for x in results)
    assert sum(bool(x[1]['already_existed']) for x in results)==1
    results=pair(lambda:settlement(demo_flow),lambda:settlement(demo_flow))
    assert all(x[0]=='ok' for x in results)
    assert sum(bool(x[1]['already_existed']) for x in results)==1


def test_refund_and_settlement_balance(demo_flow):
    _,pay=payment(demo_flow); c.create_commission_from_payment(pay['code'])
    results=pair(lambda:rf.create_refund(rf.RefundCreate(payment_code=pay['code'],amount=50,reason='Race DEMO',idempotency_key='half')),lambda:settlement(demo_flow))
    assert all(x[0]=='ok' for x in results)
    with get_connection() as conn:
        assert conn.execute('SELECT total_commission_amount FROM partner_settlements WHERE partner_id=%s',(demo_flow['partner'],)).fetchone()[0]==5


def test_cancellation_and_confirmation_have_no_untracked_money(demo_flow):
    from app.auth import Principal,_current_actor
    from app.services.cancellation_policy import CancellationPolicy
    f=demo_flow; res=reserve(f)
    ps.create_passengers(res['code'],ps.PassengersCreate(passengers=[passenger()]))
    pay=p.create_payment(p.PaymentCreate(reservation_code=res['code'],payment_method='cash',received_by='partner'))
    p.confirm_cash_by_customer(pay['code'])
    policy=api.create_policy(api.PolicyCreate(name='Race DEMO only',policy=CancellationPolicy(rules=[dict(actors=['admin'],minimum_notice_seconds=0,outcome='cancel',refund_type='percentage',refund_value='100')])))
    api.assign_policy(api.PolicyAssignment(product_id=f['product'],policy_id=policy['id']))
    with get_connection() as conn:
        conn.execute("UPDATE reservations SET service_at=clock_timestamp()+interval '1 day' WHERE id=%s",(res['id'],))
    def cancel():
        token=_current_actor.set(Principal(subject='demo-race-admin',role='admin'))
        try: return api.request_cancellation(res['code'],api.CancellationRequest(reason='DEMO race',idempotency_key='race'))
        finally: _current_actor.reset(token)
    results=pair(cancel,lambda:p.confirm_cash_by_partner(pay['code']))
    assert results[0][0]=='ok' and results[0][1]['status']=='cancelled'
    assert results[1][0]=='ok' or results[1]==('http',409)
    with get_connection() as conn:
        state=conn.execute('SELECT status FROM payments WHERE id=%s',(pay['id'],)).fetchone()[0]
        assert state in {'cancelled','paid'}
        amount=conn.execute("SELECT COALESCE(sum(amount),0) FROM refund_commands WHERE payment_id=%s AND status='pending'",(pay['id'],)).fetchone()[0]
        assert amount==(100 if state=='paid' else 0)
