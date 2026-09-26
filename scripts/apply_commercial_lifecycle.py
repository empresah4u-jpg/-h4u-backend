"""Explicit 008 runner; dry-run verifies rollback before any optional application."""
import argparse
from hashlib import sha256
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]/'db'/'migrations'
NAME='008_commercial_lifecycle.sql'


def apply(conn):
    conn.execute("SET LOCAL lock_timeout='5s'")
    conn.execute("SET LOCAL statement_timeout='30s'")
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('h4u-schema-migrations'))")
    for path in sorted(ROOT.glob('00[2-7]_*.sql')):
        row=conn.execute('SELECT checksum FROM schema_migrations WHERE version=%s',(path.name,)).fetchone()
        if row!=(sha256(path.read_bytes()).hexdigest(),): raise RuntimeError('Prerequisite mismatch: '+path.name)
    source=(ROOT/NAME).read_bytes()
    digest=sha256(source).hexdigest()
    row=conn.execute('SELECT checksum FROM schema_migrations WHERE version=%s',(NAME,)).fetchone()
    if row:
        if row!=(digest,): raise RuntimeError('008 checksum mismatch')
        return False
    conn.execute(source.decode())
    conn.execute('INSERT INTO schema_migrations(version,checksum) VALUES (%s,%s)',(NAME,digest))
    return True


def dry_run(conn):
    before=conn.execute("SELECT to_regclass('public.commercial_slots'),(SELECT count(*) FROM schema_migrations)").fetchone()
    conn.rollback()
    with conn.transaction(force_rollback=True): apply(conn)
    after=conn.execute("SELECT to_regclass('public.commercial_slots'),(SELECT count(*) FROM schema_migrations)").fetchone()
    conn.rollback()
    if after!=before: raise RuntimeError('Rollback verification failed')
    return True


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--demo',action='store_true')
    args=parser.parse_args()
    if args.demo:
        if not args.apply: raise SystemExit('Demo upgrade requires --demo --apply; dry-run is mandatory within upgrade')
        from scripts.setup_demo import upgrade_demo_schema,expected_migrations,command,CONTAINER
        if command(['docker','inspect','-f','{{index .Config.Labels "com.h4u.demo"}}',CONTAINER]).strip()!='isolated':
            raise RuntimeError('Unexpected demo container')
        upgrade_demo_schema(expected_migrations())
        print('008 demo dry-run, rollback, application and checksums verified')
        return
    from app.db import get_connection
    with get_connection() as conn:
        dry_run(conn)
        print('008 dry-run and rollback verified')
        if args.apply:
            with conn.transaction(): changed=apply(conn)
            print('008 applied' if changed else '008 already applied; checksum verified')


if __name__=='__main__': main()
