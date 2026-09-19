"""Append-only credits against historical commissions. Caller owns transaction.

Every operation affecting a partner balance locks partner before payment/commission/
settlement. This serializes refunds, commission creation and settlement allocation.
"""
from decimal import Decimal, ROUND_HALF_UP
from fastapi import HTTPException
from psycopg.types.json import Jsonb
from fastapi.encoders import jsonable_encoder
from app.auth import audit_actor

CENT = Decimal('0.01')


def lock_payment_partner(cur, payment_code):
    cur.execute('SELECT r.partner_id FROM payments p JOIN reservations r ON r.id=p.reservation_id WHERE p.code=%s', (payment_code,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, 'Pago no encontrado.')
    cur.execute('SELECT id FROM partners WHERE id=%s FOR UPDATE', (row[0],))
    if not cur.fetchone():
        raise HTTPException(409, 'Partner financiero inexistente.')
    return row[0]


def lock_settlement_partner(cur, settlement_code):
    cur.execute('SELECT partner_id FROM partner_settlements WHERE code=%s', (settlement_code,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, 'Settlement no encontrado.')
    cur.execute('SELECT id FROM partners WHERE id=%s FOR UPDATE', (row[0],))
    cur.fetchone()
    return row[0]


def event(cur, entity_type, entity_id, kind, details):
    subject, role = audit_actor()
    cur.execute('''INSERT INTO financial_events(entity_type,entity_id,event_type,actor_subject,actor_role,details)
                   VALUES (%s,%s,%s,%s,%s,%s)''',
                (entity_type, entity_id, kind, subject, role, Jsonb(jsonable_encoder(details))))


def snapshot_settlement(cur, settlement_id, kind):
    cur.execute('SELECT to_jsonb(s) FROM partner_settlements s WHERE id=%s', (settlement_id,))
    event(cur, 'settlement', settlement_id, kind, cur.fetchone()[0])


def prorated_reversal(original_commission, original_payment, cumulative_refunds):
    if (not original_payment.is_finite() or original_payment <= 0
            or not original_commission.is_finite() or original_commission < 0
            or not cumulative_refunds.is_finite()
            or not 0 <= cumulative_refunds <= original_payment):
        raise HTTPException(409, 'Base financiera inválida para prorratear.')
    # Cumulative rounding avoids penny drift and guarantees full reversal at 100%.
    return (original_commission * cumulative_refunds / original_payment).quantize(CENT, rounding=ROUND_HALF_UP)


def adjust_for_refund(cur, payment_id, refund_id, original_payment, cumulative_refunds):
    cur.execute('SELECT id,commission_amount,status FROM commissions WHERE payment_id=%s FOR UPDATE', (payment_id,))
    commission = cur.fetchone()
    if not commission:
        return None
    cid, gross, status = commission
    if status not in {'earned', 'settled'}:
        raise HTTPException(409, 'La comisión requiere conciliación antes del refund.')
    cur.execute('SELECT COALESCE(sum(amount),0) FROM commission_adjustments WHERE commission_id=%s', (cid,))
    previously_reversed = cur.fetchone()[0]
    # Refuse to silently absorb an earlier, unaccounted refund into this movement.
    cur.execute('SELECT COALESCE(sum(amount),0) FROM refunds WHERE payment_id=%s AND status=\'processed\' AND id<>%s', (payment_id,refund_id))
    previous_refunds=cur.fetchone()[0]
    if prorated_reversal(gross,original_payment,previous_refunds) != previously_reversed:
        raise HTTPException(409, 'Existen refunds históricos sin ajustes; requieren conciliación.')
    amount = prorated_reversal(gross, original_payment, cumulative_refunds) - previously_reversed
    subject, role = audit_actor()
    cur.execute('''INSERT INTO commission_adjustments(commission_id,refund_id,amount,actor_subject,actor_role)
                   VALUES (%s,%s,%s,%s,%s) RETURNING id''', (cid,refund_id,amount,subject,role))
    adjustment_id = cur.fetchone()[0]
    cur.execute('''SELECT s.id,s.status,s.total_commission_amount FROM settlement_commissions sc
                   JOIN partner_settlements s ON s.id=sc.settlement_id
                   WHERE sc.commission_id=%s FOR UPDATE OF s''', (cid,))
    settlement=cur.fetchone()
    applied=Decimal('0')
    if settlement and status == 'earned':
        if settlement[1] not in {'open','pending_payment','overdue','waiting_verification'}:
            raise HTTPException(409, 'El cierre requiere conciliación antes del refund.')
        applied=min(amount,settlement[2])
        if applied > 0:
            snapshot_settlement(cur,settlement[0],'before_refund_credit')
            cur.execute('INSERT INTO settlement_adjustments(settlement_id,adjustment_id,amount) VALUES (%s,%s,%s)', (settlement[0],adjustment_id,applied))
            cur.execute('''UPDATE partner_settlements SET
                reported_amount=CASE WHEN reported_at IS NOT NULL THEN COALESCE(reported_amount,total_commission_amount) ELSE reported_amount END,
                total_commission_amount=total_commission_amount-%s,updated_at=now() WHERE id=%s''', (applied,settlement[0]))
    event(cur,'commission',cid,'refund_credit',{'refund_id':refund_id,'adjustment_id':adjustment_id,'amount':amount,'applied':applied})
    return {'id':str(adjustment_id),'amount':float(amount),'applied_amount':float(applied),'pending_amount':float(amount-applied)}


def allocate_credits(cur, partner_id, currency, settlement_id, gross):
    # Origin must already be billed (including commissions just linked to this close).
    cur.execute('''SELECT a.id,a.amount-COALESCE((SELECT sum(sa.amount) FROM settlement_adjustments sa WHERE sa.adjustment_id=a.id),0)
        FROM commission_adjustments a JOIN commissions c ON c.id=a.commission_id
        WHERE c.partner_id=%s AND c.currency=%s
          AND (c.status='settled' OR EXISTS (SELECT 1 FROM settlement_commissions sc WHERE sc.commission_id=c.id))
        ORDER BY a.created_at,a.id FOR UPDATE OF a''', (partner_id,currency))
    available = cur.fetchall()
    net=gross
    for aid, remaining in available:
        if remaining < 0:
            raise HTTPException(409,'Crédito sobrecompensado; requiere conciliación.')
        amount=min(remaining,net)
        if amount > 0:
            cur.execute('INSERT INTO settlement_adjustments(settlement_id,adjustment_id,amount) VALUES (%s,%s,%s)', (settlement_id,aid,amount))
            net-=amount
    cur.execute('UPDATE partner_settlements SET total_commission_amount=%s,updated_at=now() WHERE id=%s', (net,settlement_id))
    event(cur,'settlement',settlement_id,'credits_allocated',{'gross':gross,'credit':gross-net,'net':net})
    return net


def validate_settlement_balance(cur, settlement_id):
    cur.execute('''SELECT s.total_commission_amount,
          COALESCE((SELECT sum(sc.commission_amount) FROM settlement_commissions sc WHERE sc.settlement_id=s.id),0),
          COALESCE((SELECT sum(sa.amount) FROM settlement_adjustments sa WHERE sa.settlement_id=s.id),0)
          FROM partner_settlements s WHERE s.id=%s''', (settlement_id,))
    net,gross,credit=cur.fetchone()
    if net != gross-credit or net < 0:
        raise HTTPException(409, 'El total del cierre no coincide con sus partidas y ajustes.')
