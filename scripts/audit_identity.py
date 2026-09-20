"""Read-only identity checks: counts/checksums only, never emails, hashes or tokens."""
from hashlib import sha256
import json
from pathlib import Path

from app.db import get_connection

CHECKS = {
    'users_with_invalid_owner': "SELECT count(*) FROM users WHERE NOT ((role='tourist' AND traveler_id IS NOT NULL AND partner_id IS NULL) OR (role='partner' AND partner_id IS NOT NULL AND traveler_id IS NULL) OR (role IN ('operator','admin') AND traveler_id IS NULL AND partner_id IS NULL))",
    'duplicate_normalized_emails': "SELECT count(*) FROM (SELECT lower(email) FROM users GROUP BY lower(email) HAVING count(*)>1) duplicates",
    'orphan_auth_sessions': "SELECT count(*) FROM auth_sessions s LEFT JOIN users u ON u.id=s.user_id WHERE u.id IS NULL",
    'invalid_session_lifetimes': "SELECT count(*) FROM auth_sessions WHERE expires_at<=created_at",
    'unrevoked_sessions_for_inactive_users': "SELECT count(*) FROM auth_sessions s JOIN users u ON u.id=s.user_id WHERE u.status<>'active' AND s.revoked_at IS NULL",
    'users_needing_argon2id_review': "SELECT count(*) FROM users WHERE password_hash NOT LIKE '$argon2id$%'",
}


def main():
    root = Path(__file__).resolve().parents[1] / 'db/migrations'
    with get_connection() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        conn.execute("SET LOCAL statement_timeout = '15s'")
        results = {name: conn.execute(sql).fetchone()[0] for name, sql in CHECKS.items()}
        results['users_count'] = conn.execute('SELECT count(*) FROM users').fetchone()[0]
        results['sessions_count'] = conn.execute('SELECT count(*) FROM auth_sessions').fetchone()[0]
        results['migrations'] = {}
        for name in ('003_identity_auth.sql', '004_auth_security.sql'):
            recorded = conn.execute('SELECT checksum FROM schema_migrations WHERE version=%s', (name,)).fetchone()
            results['migrations'][name] = recorded == (sha256((root / name).read_bytes()).hexdigest(),)
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
