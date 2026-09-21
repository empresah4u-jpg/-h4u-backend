"""Aggregate-only partner audit; no credential or personal values selected."""
from hashlib import sha256
from pathlib import Path
import json
from app.db import get_connection

CHECKS = {
    'orphan_memberships': '''SELECT count(*) FROM partner_memberships m
        LEFT JOIN users u ON u.id=m.user_id LEFT JOIN partners p ON p.id=m.partner_id
        WHERE u.id IS NULL OR p.id IS NULL''',
    'duplicate_memberships': '''SELECT count(*) FROM (SELECT partner_id,user_id FROM partner_memberships
        GROUP BY partner_id,user_id HAVING count(*)>1) d''',
    'invalid_membership_roles_states': "SELECT count(*) FROM partner_memberships WHERE membership_role NOT IN ('owner','manager','staff') OR status NOT IN ('active','suspended','revoked')",
    'orphan_partner_events': '''SELECT count(*) FROM partner_events e LEFT JOIN partners p ON p.id=e.partner_id
        LEFT JOIN partner_memberships m ON m.id=e.membership_id WHERE p.id IS NULL OR (e.membership_id IS NOT NULL AND m.id IS NULL)''',
    'membership_event_partner_mismatch': '''SELECT count(*) FROM partner_events e JOIN partner_memberships m ON m.id=e.membership_id WHERE m.partner_id<>e.partner_id''',
    'missing_creation_audit': "SELECT count(*) FROM partner_memberships m WHERE NOT EXISTS (SELECT 1 FROM partner_events e WHERE e.membership_id=m.id AND e.event_type='membership_created')",
    'invalid_suspension_source': "SELECT count(*) FROM partners WHERE suspension_source IS NOT NULL AND (status<>'suspended' OR suspension_source NOT IN ('administrative','debt','legacy'))",
}


def main():
    root = Path(__file__).resolve().parents[1]/'db/migrations'
    with get_connection() as c:
        c.execute('SET TRANSACTION READ ONLY')
        c.execute("SET LOCAL statement_timeout='15s'")
        checks = {name:c.execute(query).fetchone()[0] for name,query in CHECKS.items()}
        sums = c.execute("SELECT count(*),count(*) FILTER (WHERE role='admin'),count(*) FILTER (WHERE role='partner') FROM users").fetchone()
        migrations = {}
        for name in ('002_financial_adjustments.sql','003_identity_auth.sql','004_auth_security.sql','005_partner_memberships.sql'):
            migrations[name] = c.execute('SELECT checksum FROM schema_migrations WHERE version=%s',(name,)).fetchone() == (sha256((root/name).read_bytes()).hexdigest(),)
        stats = {'users':sums[0], 'admins':sums[1], 'partner_users':sums[2],
                 'memberships':c.execute('SELECT count(*) FROM partner_memberships').fetchone()[0],
                 'events':c.execute('SELECT count(*) FROM partner_events').fetchone()[0],
                 'auth_sessions':c.execute('SELECT count(*) FROM auth_sessions').fetchone()[0]}
        non_unique = c.execute("SELECT NOT i.indisunique FROM pg_index i JOIN pg_class x ON x.oid=i.indexrelid WHERE x.oid=to_regclass('idx_users_partner')").fetchone() == (True,)
        obsolete_absent = c.execute("SELECT to_regclass('uq_users_partner') IS NULL").fetchone()[0]
    print(json.dumps({'checks':checks,'counts':stats,'migrations':migrations,
                      'multiple_users_supported':non_unique and obsolete_absent},indent=2))
    return int(any(checks.values()) or not all(migrations.values()) or not (non_unique and obsolete_absent))


if __name__ == '__main__':
    raise SystemExit(main())
