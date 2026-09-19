"""Auditoría de solo lectura: muestra conteos, nunca datos personales."""
import json
from app.db import get_connection

CHECKS = {
    'multiple_active_payments': "SELECT count(*) FROM (SELECT reservation_id FROM payments WHERE status IN ('pending','reported','waiting_verification','paid','partially_refunded') GROUP BY reservation_id HAVING count(*) > 1) x",
    'multiple_primary_passengers': "SELECT count(*) FROM (SELECT service_request_id FROM request_passengers WHERE is_primary_passenger GROUP BY service_request_id HAVING count(*) > 1) x",
    'refund_exceeds_payment': "SELECT count(*) FROM payments p JOIN (SELECT payment_id,sum(amount) amount FROM refunds WHERE status='processed' GROUP BY payment_id) r ON r.payment_id=p.id WHERE r.amount>p.amount",
    'refund_commission_reversal_mismatch': "SELECT count(*) FROM commissions c JOIN payments p ON p.id=c.payment_id WHERE COALESCE((SELECT sum(a.amount) FROM commission_adjustments a WHERE a.commission_id=c.id),0) IS DISTINCT FROM round(c.commission_amount * COALESCE((SELECT sum(r.amount) FROM refunds r WHERE r.payment_id=p.id AND r.status='processed'),0) / NULLIF(p.amount,0),2)",
    'reservation_winner_mismatch': "SELECT count(*) FROM reservations r JOIN request_partners rp ON rp.id=r.request_partner_id WHERE r.partner_id IS DISTINCT FROM rp.partner_id OR r.service_request_id IS DISTINCT FROM rp.service_request_id OR NOT rp.is_winner OR rp.status <> 'accepted'",
    'paid_payment_cancelled_reservation': "SELECT count(*) FROM payments p JOIN reservations r ON r.id=p.reservation_id WHERE p.status='paid' AND r.status='cancelled'",
    'invalid_commission_amount': "SELECT count(*) FROM commissions WHERE commission_amount>base_amount OR commission_amount='NaN'::numeric",
    'unvalidated_constraints': "SELECT count(*) FROM pg_constraint WHERE connamespace='public'::regnamespace AND NOT convalidated",
    'settlement_total_mismatch': "SELECT count(*) FROM partner_settlements s LEFT JOIN (SELECT settlement_id,sum(commission_amount) total FROM settlement_commissions GROUP BY settlement_id) c ON c.settlement_id=s.id WHERE s.total_commission_amount IS DISTINCT FROM COALESCE(c.total,0)-COALESCE((SELECT sum(a.amount) FROM settlement_adjustments a WHERE a.settlement_id=s.id),0)",
    'adjustment_refund_payment_mismatch': "SELECT count(*) FROM commission_adjustments a JOIN commissions c ON c.id=a.commission_id JOIN refunds r ON r.id=a.refund_id WHERE c.payment_id IS DISTINCT FROM r.payment_id OR c.currency<>r.currency OR r.status<>'processed'",
    'overallocated_adjustment': "SELECT count(*) FROM commission_adjustments a WHERE COALESCE((SELECT sum(s.amount) FROM settlement_adjustments s WHERE s.adjustment_id=a.id),0)>a.amount",
    'settlement_credit_owner_currency_mismatch': "SELECT count(*) FROM settlement_adjustments a JOIN commission_adjustments ca ON ca.id=a.adjustment_id JOIN commissions c ON c.id=ca.commission_id JOIN partner_settlements s ON s.id=a.settlement_id WHERE s.partner_id<>c.partner_id OR s.currency<>c.currency",
    'negative_settlement_balance': "SELECT count(*) FROM partner_settlements WHERE total_commission_amount<0 OR total_commission_amount='NaN'::numeric",
}


def main():
    with get_connection() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        conn.execute("SET LOCAL statement_timeout = '15s'")
        results={name: conn.execute(sql).fetchone()[0] for name,sql in CHECKS.items()}
        results['extensions']=conn.execute('SELECT extname,extversion FROM pg_extension ORDER BY extname').fetchall()
        results['embedding_models']=conn.execute('SELECT embedding_model,count(*) FROM entity_embeddings GROUP BY embedding_model').fetchall()
        print(json.dumps(results,indent=2))


if __name__=='__main__':
    main()
