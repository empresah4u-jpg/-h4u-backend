from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
import psycopg
from fastapi.testclient import TestClient

from app.main import app
from app.auth import Principal
from app.routers import commissions as c, refunds as rf, settlements as st
from app.services.finance import prorated_reversal
from tests.test_commercial import flow, payment, conflict, admin_http
from tests.test_authorization import as_actor


def commission(flow, fixed=False):
    if fixed:
        flow['conn'].execute("UPDATE partners SET commission_type='fixed',commission_value=10 WHERE id=%s",(flow['partner'],))
    res,pay=payment(flow)
    return res,pay,c.create_commission_from_payment(pay['code'])


def refund(pay, amount, key=None):
    return rf.create_refund(rf.RefundCreate(payment_code=pay['code'],amount=amount,reason='Audit',idempotency_key=key or uuid4().hex))


def settlement(flow, offset=0):
    today=flow['conn'].execute('SELECT CURRENT_DATE').fetchone()[0]+timedelta(days=offset)
    return st.create_settlement(st.SettlementCreate(partner_code=flow['partner_code'],period_start=today,period_end=today,currency='PEN',due_date=today))


def settle(code):
    st.report_settlement_payment(code,st.SettlementPaymentReport(payment_method='bank_transfer',payment_reference='verified-test'))
    return st.verify_settlement_payment(code)


@pytest.mark.parametrize('fixed',[False,True])
@pytest.mark.parametrize('amount,net', [('30','7'),('100','0')])
def test_refund_before_settlement_preserves_original(flow,fixed,amount,net):
    _,pay,comm=commission(flow,fixed)
    result=refund(pay,Decimal(amount))
    assert Decimal(str(result['commission_adjustment']['amount'])) == Decimal('10')-Decimal(net)
    s=settlement(flow)
    assert Decimal(str(s['total_commission_amount']))==Decimal(net)
    if Decimal(net)==0:
        assert st.verify_settlement_payment(s['code'])['settled_without_transfer'] is True
    else:
        settle(s['code'])
    assert flow['conn'].execute('SELECT commission_amount,status FROM commissions WHERE code=%s',(comm['code'],)).fetchone()==(Decimal('10'),'settled')
    assert c.create_commission_from_payment(pay['code'])['already_existed'] is True
    assert flow['conn'].execute('SELECT commission_amount FROM settlement_commissions WHERE settlement_id=%s',(s['id'],)).fetchone()[0]==10  # Historical gross retained.


def test_refund_after_paid_settlement_carries_credit(flow):
    _,pay,comm=commission(flow)
    first=settlement(flow)
    settle(first['code'])
    result=refund(pay,100)
    assert result['commission_adjustment']['pending_amount']==10
    assert flow['conn'].execute('SELECT total_commission_amount,status FROM partner_settlements WHERE code=%s',(first['code'],)).fetchone()==(Decimal('10'),'paid')
    flow['conn'].execute('UPDATE product_partners SET partner_price=30 WHERE partner_id=%s',(flow['partner'],))
    commission(flow)
    second=settlement(flow,1)
    assert second['total_commission_amount']==0 and second['credit_amount']==3
    assert st.verify_settlement_payment(second['code'])['settled_without_transfer'] is True
    flow['conn'].execute('UPDATE product_partners SET partner_price=200 WHERE partner_id=%s',(flow['partner'],))
    commission(flow)
    third=settlement(flow,2)
    assert third['total_commission_amount']==13 and third['credit_amount']==7
    settle(third['code'])
    db=flow['conn']
    assert db.execute('SELECT sum(sa.amount) FROM settlement_adjustments sa JOIN commission_adjustments a ON a.id=sa.adjustment_id WHERE a.refund_id=%s',(result['id'],)).fetchone()[0]==10
    assert db.execute('SELECT commission_amount FROM commissions WHERE code=%s',(comm['code'],)).fetchone()[0]==10


def test_refund_adjusts_unpaid_existing_settlement(flow):
    _,pay,_=commission(flow)
    s=settlement(flow)
    result=refund(pay,30)
    assert result['commission_adjustment']['applied_amount']==3
    assert flow['conn'].execute('SELECT total_commission_amount FROM partner_settlements WHERE code=%s',(s['code'],)).fetchone()[0]==7
    assert settle(s['code'])['total_commission_amount']==7


@pytest.mark.parametrize('amount',[30,100])
def test_refund_requires_reconciliation_of_prior_report(flow,amount):
    _,pay,_=commission(flow)
    s=settlement(flow)
    st.report_settlement_payment(s['code'],st.SettlementPaymentReport(payment_method='bank_transfer',payment_reference='original-report'))
    refund(pay,amount)
    conflict(st.verify_settlement_payment,s['code'])
    st.report_settlement_payment(s['code'],st.SettlementPaymentReport(payment_method='other',payment_reference='reconciled-report'))
    assert st.verify_settlement_payment(s['code'])['total_commission_amount']==10-amount/10
    row=flow['conn'].execute("SELECT count(*) FROM financial_events WHERE entity_id=%s AND details->>'payment_reference'='original-report'",(s['id'],)).fetchone()
    assert row[0]>=1


def test_refund_replay_and_key_conflict(flow):
    _,pay,_=commission(flow)
    first=refund(pay,100,key='same-key')
    assert refund(pay,Decimal('100.00'),key='same-key')==first
    conflict(refund,pay,50,'same-key')
    db=flow['conn']
    assert db.execute('SELECT count(*) FROM refunds WHERE payment_id=%s',(pay['id'],)).fetchone()[0]==1
    assert db.execute('SELECT count(*) FROM commission_adjustments WHERE refund_id=%s',(first['id'],)).fetchone()[0]==1


def test_cumulative_rounding(flow):
    flow['conn'].execute('UPDATE partners SET commission_value=0.01 WHERE id=%s',(flow['partner'],))
    _,pay=payment(flow)
    comm=c.create_commission_from_payment(pay['code'])
    assert comm['commission_amount']==0.01
    assert refund(pay,33)['commission_adjustment']['amount']==0
    assert refund(pay,33)['commission_adjustment']['amount']==0.01
    assert refund(pay,34)['commission_adjustment']['amount']==0
    assert settlement(flow)['total_commission_amount']==0


def test_financial_ledger_is_immutable(flow):
    _,pay,_=commission(flow)
    result=refund(pay,20)
    db=flow['conn']
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        with db.transaction():
            db.execute('UPDATE commission_adjustments SET amount=0 WHERE id=%s',(result['commission_adjustment']['id'],))
    assert db.execute('SELECT amount FROM commission_adjustments WHERE id=%s',(result['commission_adjustment']['id'],)).fetchone()[0]==2


def test_overallocated_adjustment_rejected_by_database(flow):
    _,pay,_=commission(flow)
    result=refund(pay,20)
    s=settlement(flow)
    # Even a separate settlement cannot spend the same credit twice.
    commission(flow)
    other=settlement(flow,1)
    with pytest.raises(psycopg.errors.CheckViolation):
        with flow['conn'].transaction():
            flow['conn'].execute('INSERT INTO settlement_adjustments(settlement_id,adjustment_id,amount) VALUES (%s,%s,1)',(other['id'],result['commission_adjustment']['id']))


def test_http_refund_actor_audit_and_key_requirement(flow):
    _,pay,_=commission(flow)
    with as_actor(Principal(subject='verified-operator-42',role='operator')) as client:
        body={'payment_code':pay['code'],'amount':20,'reason':'Audit'}
        assert client.post('/refunds',json=body).status_code==400
        body['idempotency_key']='audit-http-refund'
        response=client.post('/refunds',json=body)
        assert response.status_code==201
        assert client.post('/refunds',json=body).json()==response.json()
    row=flow['conn'].execute('SELECT actor_subject,actor_role FROM refunds WHERE id=%s',(response.json()['id'],)).fetchone()
    assert row==('verified-operator-42','operator')


def test_refund_transaction_rolls_back_on_invalid_commission(flow):
    _,pay,comm=commission(flow)
    flow['conn'].execute("UPDATE commissions SET status='disputed' WHERE code=%s",(comm['code'],))
    conflict(refund,pay,20)
    assert flow['conn'].execute('SELECT count(*) FROM refunds WHERE payment_id=%s',(pay['id'],)).fetchone()[0]==0
    assert flow['conn'].execute('SELECT status FROM payments WHERE id=%s',(pay['id'],)).fetchone()[0]=='paid'


def test_balance_corruption_blocks_verification(flow):
    _,_,_=commission(flow)
    s=settlement(flow)
    st.report_settlement_payment(s['code'],st.SettlementPaymentReport(payment_method='cash'))
    flow['conn'].execute('UPDATE partner_settlements SET total_commission_amount=9 WHERE code=%s',(s['code'],))
    conflict(st.verify_settlement_payment,s['code'])
