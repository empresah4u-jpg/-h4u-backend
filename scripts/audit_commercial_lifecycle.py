"""Read-only counts. Never repairs a financial inconsistency."""
import argparse
import json

CHECKS={
 'confirmed_passenger_mismatch': "SELECT count(*) FROM reservations r WHERE r.status IN ('confirmed','ready','completed','no_show') AND r.passenger_count<>(SELECT count(*) FROM request_passengers rp WHERE rp.service_request_id=r.service_request_id)",
 'confirmed_without_required_payment': "SELECT count(*) FROM reservations r JOIN products p ON p.id=r.product_id WHERE r.status IN ('confirmed','ready','completed','no_show') AND p.requires_payment AND NOT EXISTS(SELECT 1 FROM payments py WHERE py.reservation_id=r.id AND py.status IN ('paid','partially_refunded','refunded'))",

 'automatic_commission_without_payment': "SELECT count(*) FROM commissions WHERE trigger_event='payment_confirmed' AND payment_id IS NULL",
 'commission_payment_provenance': "SELECT count(*) FROM commissions c JOIN payments p ON p.id=c.payment_id JOIN reservations r ON r.id=p.reservation_id WHERE c.reservation_id<>p.reservation_id OR c.partner_id<>r.partner_id OR c.currency<>p.currency OR p.status NOT IN ('paid','partially_refunded','refunded')",
 'orphan_financial_events': "SELECT count(*) FROM financial_events e WHERE (e.entity_type='payment' AND NOT EXISTS(SELECT 1 FROM payments WHERE id=e.entity_id)) OR (e.entity_type='refund' AND NOT EXISTS(SELECT 1 FROM refunds WHERE id=e.entity_id)) OR (e.entity_type='commission' AND NOT EXISTS(SELECT 1 FROM commissions WHERE id=e.entity_id)) OR (e.entity_type='settlement' AND NOT EXISTS(SELECT 1 FROM partner_settlements WHERE id=e.entity_id))",

 'duplicate_reservation': 'SELECT count(*) FROM (SELECT service_request_id FROM reservations GROUP BY service_request_id HAVING count(*)>1) x',
 'duplicate_commission': 'SELECT count(*) FROM (SELECT payment_id FROM commissions WHERE payment_id IS NOT NULL GROUP BY payment_id HAVING count(*)>1) x',
 'duplicate_settlement_allocation': 'SELECT count(*) FROM (SELECT commission_id FROM settlement_commissions GROUP BY commission_id HAVING count(*)>1) x',
 'slot_occupancy_mismatch': "SELECT count(*) FROM commercial_slots s WHERE s.reserved<>(SELECT COALESCE(sum(r.passenger_count),0) FROM reservations r WHERE r.slot_id=s.id AND r.status NOT IN ('cancelled','expired')) OR s.reserved>s.capacity OR s.reserved<0",
 'refund_command_exceeds_balance': "SELECT count(*) FROM payments p WHERE p.amount<COALESCE((SELECT sum(amount) FROM refunds WHERE payment_id=p.id AND status='processed'),0)+COALESCE((SELECT sum(amount) FROM refund_commands WHERE payment_id=p.id AND status='pending'),0)",
 'refund_command_provenance': "SELECT count(*) FROM refund_commands c JOIN cancellation_cases ca ON ca.id=c.cancellation_id JOIN payments p ON p.id=c.payment_id LEFT JOIN refunds r ON r.id=c.refund_id WHERE p.reservation_id<>ca.reservation_id OR c.currency<>p.currency OR (c.status='processed' AND (r.id IS NULL OR r.payment_id<>p.id OR r.amount<>c.amount OR r.status<>'processed'))",
 'cancelled_with_unresolved_payment': "SELECT count(*) FROM payments p JOIN reservations r ON r.id=p.reservation_id WHERE r.status='cancelled' AND p.status IN ('paid','partially_refunded','disputed') AND NOT EXISTS(SELECT 1 FROM cancellation_cases c WHERE c.reservation_id=r.id AND c.status='cancelled')",
 'expired_with_collected_payment': "SELECT count(*) FROM reservations r JOIN payments p ON p.reservation_id=r.id WHERE r.status='expired' AND p.status IN ('paid','partially_refunded','disputed')",
}


def audit(conn):
    from scripts.audit_database import CHECKS as BASE
    checks={**BASE,**CHECKS}
    # A configured non-refundable cancellation or pending external refund is valid.
    checks.pop('paid_payment_cancelled_reservation')
    conn.execute('SET TRANSACTION READ ONLY')
    conn.execute("SET LOCAL statement_timeout='15s'")
    return {name:conn.execute(sql).fetchone()[0] for name,sql in checks.items()}


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--demo',action='store_true')
    args=parser.parse_args()
    if args.demo:
        from app.demo.safety import configure
        configure()
    from app.db import get_connection
    with get_connection() as conn: results=audit(conn)
    print(json.dumps(results,indent=2))
    if any(results.values()): raise SystemExit(1)


if __name__=='__main__': main()
