"""Apply only additive 004 after verifying applied 003; never reruns/edits 003."""
from hashlib import sha256
from pathlib import Path
from app.db import get_connection

ROOT = Path(__file__).resolve().parents[1] / 'db/migrations'


def main():
    source = (ROOT / '004_auth_security.sql').read_bytes()
    checksum = sha256(source).hexdigest()
    with get_connection() as conn:
        conn.execute("SET LOCAL lock_timeout = '5s'")
        conn.execute("SET LOCAL statement_timeout = '30s'")
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('h4u-schema-migrations'))")
        previous = conn.execute('SELECT checksum FROM schema_migrations WHERE version=%s',
                                ('003_identity_auth.sql',)).fetchone()
        if previous != (sha256((ROOT / '003_identity_auth.sql').read_bytes()).hexdigest(),):
            raise RuntimeError('003 must already be applied with its original checksum')
        applied = conn.execute('SELECT checksum FROM schema_migrations WHERE version=%s',
                               ('004_auth_security.sql',)).fetchone()
        if applied:
            if applied != (checksum,):
                raise RuntimeError('004 checksum mismatch; refusing to change applied migration')
            print('004 already applied; checksum verified')
            return
        conn.execute(source.decode())
        conn.execute('INSERT INTO schema_migrations(version,checksum) VALUES (%s,%s)',
                     ('004_auth_security.sql', checksum))
    print('004 applied atomically; 003 and existing users/sessions unchanged')


if __name__ == '__main__':
    main()
