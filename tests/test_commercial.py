"""SQL real; todos los registros propios se revierten al finalizar cada prueba.

No ejecuta process-overdue (operación global) sobre la base compartida.
"""
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.db import get_connection
from app.main import app
from app.routers import (service_requests as sr, partner_responses as pr,
                        reservations as r, passengers as ps, payments as p,
                        refunds as rf, commissions as c, settlements as st, lifecycle as lc)


@pytest.fixture
def admin_http():
    from app.auth import get_current_actor, Principal
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_actor] = lambda: Principal(subject='test-admin', role='admin')
    try:
        yield
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.fixture
def flow(monkeypatch):
    conn = get_connection()
    try:
        with conn.transaction(force_rollback=True):
            conn.execute("SET LOCAL lock_timeout = '3s'")
            conn.execute("SET LOCAL statement_timeout = '10s'")
            if conn.execute("SELECT to_regclass('commission_adjustments')").fetchone()[0] is None:
                conn.execute(Path('db/migrations/002_financial_adjustments.sql').read_text())

            class Connection:
                def cursor(self):
                    return conn.cursor()
                def commit(self):
                    pass  # Los commits del endpoint liberan solo el savepoint.

            @contextmanager
            def connection():
                with conn.transaction():
                    yield Connection()

            for module in (sr, pr, r, ps, p, rf, c, st, lc):
                monkeypatch.setattr(module, 'get_connection', connection)
            if conn.execute("SELECT to_regclass('commercial_slots')").fetchone()[0] is None:
                conn.execute(Path('db/migrations/008_commercial_lifecycle.sql').read_text())
            token = uuid4().hex[:12]
            destination = conn.execute("INSERT INTO destinations(code,name,slug) VALUES (%s,'Audit',%s) RETURNING id", (token, token)).fetchone()[0]
            traveler = conn.execute("INSERT INTO travelers DEFAULT VALUES RETURNING id").fetchone()[0]
            session = conn.execute("INSERT INTO sessions(traveler_id,destination_id,channel) VALUES (%s,%s,'test') RETURNING id", (traveler, destination)).fetchone()[0]
            product = conn.execute("INSERT INTO products(destination_id,code,name,slug,product_type,status,reservations_enabled,requires_payment) VALUES (%s,%s,'Audit',%s,'tour','active',true,true) RETURNING id", (destination, token, token)).fetchone()[0]
            partner = conn.execute("INSERT INTO partners(code,business_name,status,reservations_enabled,commission_value) VALUES (%s,'Audit','active',true,10) RETURNING id", (token,)).fetchone()[0]
            conn.execute("INSERT INTO product_partners(product_id,partner_id,partner_price) VALUES (%s,%s,100)", (product, partner))
            yield dict(conn=conn, session=session, product=product, partner=partner, partner_code=token)
    finally:
        conn.close()


def request(flow, count=1):
    return sr.create_service_request(sr.ServiceRequestCreate(
        session_id=flow['session'], product_id=flow['product'],
        service_date=date.today()+timedelta(days=7), passenger_count=count,
        adults_count=count, minors_count=0))


def reserve(flow, count=1):
    req = request(flow, count)
    pr.respond_to_request(pr.PartnerResponseCreate(request_partner_id=req['candidates'][0]['request_partner_id'], action='accept'))
    return r.create_reservation(r.ReservationCreate(service_request_code=req['code']))


def passenger(number=1, primary=False, document=None):
    return ps.PassengerCreate(passenger_number=number, first_name='Test', last_name='Audit',
        birth_date=date(1990,1,1), nationality_code='PE', document_type='passport',
        document_number=document or str(number), is_primary_passenger=primary)


def payment(flow, customer_first=False):
    res = reserve(flow)
    ps.create_passengers(res['code'], ps.PassengersCreate(passengers=[passenger()]))
    pay = p.create_payment(p.PaymentCreate(reservation_code=res['code'],payment_method='cash',received_by='partner'))
    steps = (p.confirm_cash_by_customer, p.confirm_cash_by_partner) if customer_first else (p.confirm_cash_by_partner, p.confirm_cash_by_customer)
    assert steps[0](pay['code'])['paid'] is False
    assert steps[1](pay['code'])['paid'] is True
    return res, pay


def conflict(fn, *args, status=409):
    with pytest.raises(HTTPException) as exc:
        fn(*args)
    assert exc.value.status_code == status


@pytest.mark.parametrize('customer_first', [False, True])
def test_cash_to_settlement(flow, customer_first):
    res, pay = payment(flow, customer_first)
    db = flow['conn']
    assert db.execute('SELECT status FROM reservations WHERE code=%s',(res['code'],)).fetchone()[0] == 'confirmed'
    conflict(p.confirm_cash_by_partner, pay['code'])
    conflict(p.confirm_cash_by_customer, pay['code'])
    commission = c.create_commission_from_payment(pay['code'])
    assert commission['commission_amount'] == 10
    assert c.create_commission_from_payment(pay['code'])['already_existed'] is True
    today = db.execute('SELECT CURRENT_DATE').fetchone()[0]
    payload = st.SettlementCreate(partner_code=flow['partner_code'],period_start=today,period_end=today,due_date=today,currency='PEN')
    settlement = st.create_settlement(payload)
    assert settlement['total_commission_amount'] == 10
    assert st.create_settlement(payload)['already_existed'] is True
    conflict(st.verify_settlement_payment, settlement['code'])
    st.report_settlement_payment(settlement['code'],st.SettlementPaymentReport(payment_method='bank_transfer'))
    result = st.verify_settlement_payment(settlement['code'])
    assert result['paid'] is True and result['settled_commissions'] == 1
    assert st.verify_settlement_payment(settlement['code'])['already_verified'] is True


def test_reservation_duplicate_and_cancel(flow):
    res = reserve(flow)
    conflict(r.create_reservation, r.ReservationCreate(service_request_code=res['service_request']))
    result = approved_cancellation(flow, res)
    assert result['status'] == 'cancelled'
    conflict(r.cancel_reservation,res['code'],r.ReservationCancel(reason='Again'))
    conflict(ps.create_passengers,res['code'],ps.PassengersCreate(passengers=[passenger()]))


def test_payment_duplicate_and_cancel(flow):
    res = reserve(flow)
    ps.create_passengers(res['code'], ps.PassengersCreate(passengers=[passenger()]))
    payload = p.PaymentCreate(reservation_code=res['code'],payment_method='cash',received_by='partner')
    pay = p.create_payment(payload)
    conflict(p.create_payment,payload)
    approved_cancellation(flow, res)
    conflict(p.confirm_cash_by_customer,pay['code'])
    assert flow['conn'].execute('SELECT status FROM payments WHERE code=%s',(pay['code'],)).fetchone()[0] == 'cancelled'


def test_passengers_across_batches(flow):
    res = reserve(flow,2)
    ps.create_passengers(res['code'], ps.PassengersCreate(passengers=[passenger(1, True)]))
    conflict(ps.create_passengers,res['code'],ps.PassengersCreate(passengers=[passenger(2, True)]),status=400)
    conflict(ps.create_passengers,res['code'],ps.PassengersCreate(passengers=[passenger(2, document='1')]),status=400)
    result=ps.create_passengers(res['code'],ps.PassengersCreate(passengers=[passenger(2)]))
    assert result['status']=='payment_pending' and result['registered_passengers']==2


def test_refund_partial_duplicate_excess_and_total(flow):
    res,pay = payment(flow)
    payload=rf.RefundCreate(payment_code=pay['code'],amount=Decimal('30'),reason='Audit',external_reference='audit-1')
    assert rf.create_refund(payload)['payment_status']=='partially_refunded'
    conflict(rf.create_refund,payload)
    conflict(rf.create_refund,rf.RefundCreate(payment_code=pay['code'],amount=71,reason='Audit'))
    assert r.cancel_reservation(res['code'],r.ReservationCancel(reason='Audit'))['status']=='requires_manual_review'
    result=rf.create_refund(rf.RefundCreate(payment_code=pay['code'],amount=70,reason='Audit'))
    assert result['remaining_amount']==0 and result['payment_status']=='refunded'
    conflict(rf.create_refund,rf.RefundCreate(payment_code=pay['code'],amount=1,reason='Audit'))
    conflict(c.create_commission_from_payment,pay['code'])


@pytest.mark.parametrize('value', ['101', 'NaN'])
def test_invalid_commission_configuration(flow,value):
    _,pay=payment(flow)
    flow['conn'].execute('UPDATE partners SET commission_value=%s WHERE id=%s',(Decimal(value),flow['partner']))
    conflict(c.create_commission_from_payment,pay['code'])


@pytest.mark.parametrize('fn,args', [
    (r.create_reservation,(r.ReservationCreate(service_request_code='missing'),)),
    (p.create_payment,(p.PaymentCreate(reservation_code='missing',payment_method='cash',received_by='partner'),)),
    (rf.create_refund,(rf.RefundCreate(payment_code='missing',amount=1,reason='Audit'),)),
    (c.create_commission_from_payment,('missing',)),
    (st.verify_settlement_payment,('missing',)),
    (ps.create_passengers,('missing',ps.PassengersCreate(passengers=[passenger()]))),
])
def test_missing_relationships(flow,fn,args):
    conflict(fn,*args,status=404)


@pytest.mark.parametrize('amount', [0,-1,'NaN','Infinity','0.001','10000000000'])
def test_invalid_refund_amount_http(amount, admin_http):
    response=TestClient(app).post('/refunds',json={'payment_code':'missing','amount':amount,'reason':'Audit'})
    assert response.status_code==422


@pytest.mark.parametrize('change', [{'first_name':' '},{'last_name':''},{'document_number':' '},{'birth_date':'2999-01-01'}])
def test_invalid_passenger_http(change, admin_http):
    data=passenger().model_dump(mode='json')
    data.update(change)
    assert TestClient(app).post('/reservations/missing/passengers',json={'passengers':[data]}).status_code==422


def test_response_after_assignment(flow):
    req=request(flow)
    payload=pr.PartnerResponseCreate(request_partner_id=req['candidates'][0]['request_partner_id'],action='accept')
    pr.respond_to_request(payload)
    for action in ('reject','counter_offer'):
        conflict(pr.respond_to_request,pr.PartnerResponseCreate(request_partner_id=payload.request_partner_id,action=action,partner_message='Audit'))
    assert flow['conn'].execute('SELECT status,is_winner FROM request_partners WHERE id=%s',(payload.request_partner_id,)).fetchone()==('accepted',True)


@pytest.mark.parametrize('status',['created','cancelled','expired'])
def test_reservation_requires_assignment(flow,status):
    req=request(flow)
    flow['conn'].execute('UPDATE service_requests SET status=%s WHERE code=%s',(status,req['code']))
    conflict(r.create_reservation,r.ReservationCreate(service_request_code=req['code']))


@pytest.mark.parametrize('price',['0','NaN'])
def test_payment_invalid_stored_amount(flow,price):
    res=reserve(flow)
    ps.create_passengers(res['code'],ps.PassengersCreate(passengers=[passenger()]))
    flow['conn'].execute('UPDATE reservations SET agreed_price=%s WHERE code=%s',(Decimal(price),res['code']))
    conflict(p.create_payment,p.PaymentCreate(reservation_code=res['code'],payment_method='cash',received_by='partner'))


def test_unpaid_refund_and_commission(flow):
    res=reserve(flow)
    ps.create_passengers(res['code'],ps.PassengersCreate(passengers=[passenger()]))
    pay=p.create_payment(p.PaymentCreate(reservation_code=res['code'],payment_method='cash',received_by='partner'))
    conflict(rf.create_refund,rf.RefundCreate(payment_code=pay['code'],amount=1,reason='Audit'))
    conflict(c.create_commission_from_payment,pay['code'])


def test_transaction_rollback_after_payment_update(flow,monkeypatch):
    res=reserve(flow)
    ps.create_passengers(res['code'],ps.PassengersCreate(passengers=[passenger()]))
    pay=p.create_payment(p.PaymentCreate(reservation_code=res['code'],payment_method='cash',received_by='partner'))
    p.confirm_cash_by_partner(pay['code'])
    # Simular rechazo de la transición de reserva tras escribir el pago.
    conn=flow['conn']
    # No instalar triggers sobre tablas reales: envolver cursor solo para el fallo.
    original=p.get_connection
    @contextmanager
    def failing_connection():
        with original() as wrapped:
            class Cursor:
                def __enter__(self):
                    self.cursor=wrapped.cursor().__enter__()
                    return self
                def __exit__(self,*args):
                    return self.cursor.__exit__(*args)
                def execute(self,sql,params=None):
                    self.blocked='UPDATE reservations' in sql
                    if not self.blocked:
                        self.cursor.execute(sql,params)
                def fetchone(self):
                    return None if self.blocked else self.cursor.fetchone()
            class Proxy:
                def cursor(self): return Cursor()
                def commit(self): wrapped.commit()
            yield Proxy()
    monkeypatch.setattr(p,'get_connection',failing_connection)
    conflict(p.confirm_cash_by_customer,pay['code'])
    assert conn.execute('SELECT status,customer_confirmed_at FROM payments WHERE code=%s',(pay['code'],)).fetchone()==('waiting_verification',None)


@pytest.mark.parametrize('currency',['12A','PENN','€€€'])
def test_invalid_settlement_currency(currency):
    conflict(st.create_settlement,st.SettlementCreate(partner_code='missing',period_start=date.today(),period_end=date.today(),due_date=date.today(),currency=currency),status=400)


def test_invalid_settlement_period():
    conflict(st.create_settlement,st.SettlementCreate(partner_code='missing',period_start=date.today(),period_end=date.today()-timedelta(days=1),due_date=date.today(),currency='PEN'),status=400)


def test_process_overdue_isolated_temporary_tables(flow):
    # Tablas temporales que ocultan las reales solo en esta conexión.
    # La transacción exterior revierte también su creación; no hay DROP ni commit.
    db=flow['conn']
    db.execute('CREATE TEMP TABLE partners (LIKE public.partners INCLUDING ALL)')
    db.execute('CREATE TEMP TABLE partner_settlements (LIKE public.partner_settlements INCLUDING ALL)')
    partner=db.execute("INSERT INTO partners(code,business_name,status) VALUES ('audit-temp','Audit','active') RETURNING id").fetchone()[0]
    db.execute("""INSERT INTO partner_settlements(code,partner_id,period_start,period_end,due_date,currency,total_commission_amount,status)
                  VALUES ('audit-overdue',%s,CURRENT_DATE-3,CURRENT_DATE-2,CURRENT_DATE-1,'PEN',10,'pending_payment')""",(partner,))
    result=st.process_overdue_settlements()
    assert result['processed_settlements']==1 and result['suspended_partners']==1
    assert db.execute('SELECT status FROM partners').fetchone()[0]=='suspended'
    assert st.process_overdue_settlements()['processed_settlements']==0


def approved_cancellation(flow, res, reason='Audit'):
    """Explicit test-only policy; no implicit cancellation economics in production."""
    from app.auth import _current_actor, Principal
    from psycopg.types.json import Jsonb
    config={'schema_version':1,'rules':[dict(actors=['admin'],minimum_notice_seconds=0,
        outcome='cancel',refund_type='none',refund_value='0',currency=None)]}
    db=flow['conn']
    pid=db.execute("INSERT INTO cancellation_policy_versions(name,rules,actor_subject) VALUES ('test',%s,'test') RETURNING id",(Jsonb(config),)).fetchone()[0]
    db.execute('INSERT INTO cancellation_policy_assignments(product_id,policy_id) VALUES (%s,%s)',(flow['product'],pid))
    db.execute("UPDATE reservations SET service_at=clock_timestamp()+interval '1 day' WHERE id=%s",(res['id'],))
    token=_current_actor.set(Principal(subject='test-cancellation-admin',role='admin'))
    try: return r.cancel_reservation(res['code'],r.ReservationCancel(reason=reason))
    finally: _current_actor.reset(token)
