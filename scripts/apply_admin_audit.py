"""Explicit 006 runner: atomic, checksum-protected, no startup migrations."""
from hashlib import sha256
from app.db import get_connection
from scripts.apply_partner_memberships import ROOT

NAME = '006_admin_audit.sql'


def apply(conn):
    conn.execute("SET LOCAL lock_timeout='5s'")
    conn.execute("SET LOCAL statement_timeout='30s'")
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('h4u-schema-migrations'))")
    for name in ('002_financial_adjustments.sql','003_identity_auth.sql','004_auth_security.sql','005_partner_memberships.sql'):
        if conn.execute('SELECT checksum FROM schema_migrations WHERE version=%s',(name,)).fetchone() != (sha256((ROOT/name).read_bytes()).hexdigest(),):
            raise RuntimeError('Prerequisite checksum mismatch: '+name)
    source=(ROOT/NAME).read_bytes()
    checksum=sha256(source).hexdigest()
    row=conn.execute('SELECT checksum FROM schema_migrations WHERE version=%s',(NAME,)).fetchone()
    if row:
        if row!=(checksum,): raise RuntimeError('006 checksum mismatch')
        return False
    conn.execute(source.decode())
    conn.execute('INSERT INTO schema_migrations(version,checksum) VALUES (%s,%s)',(NAME,checksum))
    return True


def main():
    with get_connection() as conn:
        changed=apply(conn)
    print('006 applied atomically' if changed else '006 already applied; checksum verified')


if __name__=='__main__': main()
