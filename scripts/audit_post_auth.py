"""Read-only aggregate integrity checks; never selects credentials or personal values."""
from hashlib import sha256
import json
from pathlib import Path

from app.db import get_connection
from scripts.audit_database import CHECKS as FINANCIAL_CHECKS
from scripts.audit_identity import CHECKS as IDENTITY_CHECKS

CHECKS = {**FINANCIAL_CHECKS, **{name: sql for name, sql in IDENTITY_CHECKS.items()
                              if name != 'users_needing_argon2id_review'}}
CHECKS.update({
    'orphan_identity_owners': '''SELECT count(*) FROM users u
        LEFT JOIN travelers t ON t.id=u.traveler_id LEFT JOIN partners p ON p.id=u.partner_id
        WHERE (u.traveler_id IS NOT NULL AND t.id IS NULL) OR (u.partner_id IS NOT NULL AND p.id IS NULL)''',
    'request_session_owner_mismatch': '''SELECT count(*) FROM service_requests r JOIN sessions s ON s.id=r.session_id
        WHERE r.traveler_id IS DISTINCT FROM s.traveler_id OR r.destination_id IS DISTINCT FROM s.destination_id''',
    'candidate_product_partner_mismatch': '''SELECT count(*) FROM request_partners r
        JOIN product_partners pp ON pp.id=r.product_partner_id JOIN service_requests sr ON sr.id=r.service_request_id
        WHERE r.partner_id IS DISTINCT FROM pp.partner_id OR sr.product_id IS DISTINCT FROM pp.product_id''',
    'reservation_request_owner_mismatch': '''SELECT count(*) FROM reservations r JOIN service_requests s ON s.id=r.service_request_id
        WHERE r.traveler_id IS DISTINCT FROM s.traveler_id OR r.product_id IS DISTINCT FROM s.product_id
           OR r.partner_id IS DISTINCT FROM s.assigned_partner_id''',
    'commission_payment_owner_mismatch': '''SELECT count(*) FROM commissions c
        JOIN payments p ON p.id=c.payment_id JOIN reservations r ON r.id=p.reservation_id
        WHERE c.reservation_id IS DISTINCT FROM r.id OR c.partner_id IS DISTINCT FROM r.partner_id OR c.currency<>p.currency''',
    'settlement_commission_owner_mismatch': '''SELECT count(*) FROM settlement_commissions sc
        JOIN commissions c ON c.id=sc.commission_id JOIN partner_settlements s ON s.id=sc.settlement_id
        WHERE c.partner_id<>s.partner_id OR c.currency<>s.currency OR c.commission_amount<>sc.commission_amount''',
    'orphan_financial_events': '''SELECT count(*) FROM financial_events e
        WHERE (e.entity_type='refund' AND NOT EXISTS (SELECT 1 FROM refunds r WHERE r.id=e.entity_id))
           OR (e.entity_type='commission' AND NOT EXISTS (SELECT 1 FROM commissions c WHERE c.id=e.entity_id))
           OR (e.entity_type='settlement' AND NOT EXISTS (SELECT 1 FROM partner_settlements s WHERE s.id=e.entity_id))''',
})


def main():
    with get_connection() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        conn.execute("SET LOCAL statement_timeout='15s'")
        checks = {name: conn.execute(sql).fetchone()[0] for name, sql in CHECKS.items()}
        counts = {name: conn.execute(sql).fetchone()[0] for name, sql in {
            'users': 'SELECT count(*) FROM users',
            'admins': "SELECT count(*) FROM users WHERE role='admin'",
            'sessions': 'SELECT count(*) FROM auth_sessions',
        }.items()}
        migrations = {}
        for name in ('003_identity_auth.sql', '004_auth_security.sql'):
            row = conn.execute('SELECT checksum FROM schema_migrations WHERE version=%s', (name,)).fetchone()
            migrations[name] = row == (sha256((Path('db/migrations') / name).read_bytes()).hexdigest(),)
        # Schema metadata only: foreign keys, checks, uniqueness, no row contents.
        constraints = conn.execute("""SELECT cl.relname,co.contype,count(*) FROM pg_constraint co
            JOIN pg_class cl ON cl.oid=co.conrelid WHERE cl.relnamespace='public'::regnamespace
            AND cl.relname=ANY(%s) GROUP BY cl.relname,co.contype ORDER BY cl.relname,co.contype""",
            (['users','auth_sessions','travelers','partners','service_requests','request_partners',
              'reservations','payments','refunds','commissions','partner_settlements',
              'settlement_commissions','commission_adjustments','settlement_adjustments','financial_events'],)).fetchall()
    print(json.dumps({'checks': checks, 'counts': counts, 'migrations': migrations, 'constraints': constraints}, indent=2))
    return int(any(checks.values()) or not all(migrations.values()))


if __name__ == '__main__':
    raise SystemExit(main())
