"""Explicit atomic migration, checksum-protected; never changes an applied file."""
from hashlib import sha256
from pathlib import Path
from app.db import get_connection

ROOT = Path(__file__).resolve().parents[1] / 'db/migrations'
NAME = '005_partner_memberships.sql'


def apply(conn):
    conn.execute("SET LOCAL lock_timeout='5s'")
    conn.execute("SET LOCAL statement_timeout='30s'")
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('h4u-schema-migrations'))")
    for name in ('002_financial_adjustments.sql','003_identity_auth.sql','004_auth_security.sql'):
        if conn.execute('SELECT checksum FROM schema_migrations WHERE version=%s',(name,)).fetchone() != (sha256((ROOT/name).read_bytes()).hexdigest(),):
            raise RuntimeError('Previous migration checksum mismatch: '+name)
    # 001 may predate the migration registry; if registered, verify it too.
    old = conn.execute('SELECT checksum FROM schema_migrations WHERE version=%s',('001_create_refunds.sql',)).fetchone()
    if old and old != (sha256((ROOT/'001_create_refunds.sql').read_bytes()).hexdigest(),):
        raise RuntimeError('001 checksum mismatch')
    source = (ROOT/NAME).read_bytes()
    checksum = sha256(source).hexdigest()
    previous = conn.execute('SELECT checksum FROM schema_migrations WHERE version=%s',(NAME,)).fetchone()
    if previous:
        if previous != (checksum,):
            raise RuntimeError('005 checksum mismatch')
        return False
    conn.execute(source.decode())
    conn.execute('INSERT INTO schema_migrations(version,checksum) VALUES (%s,%s)',(NAME,checksum))
    return True


def main():
    with get_connection() as conn:
        changed = apply(conn)
    print('005 applied atomically' if changed else '005 already applied; checksum verified')


if __name__ == '__main__':
    main()
