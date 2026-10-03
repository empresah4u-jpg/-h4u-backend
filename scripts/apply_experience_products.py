"""012 official runner. Defaults to rollback rehearsal; persistence requires --apply."""
import argparse
from hashlib import sha256
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1] / 'db/migrations'
NAME = '012_experience_products.sql'
LOCKS = "SET LOCAL lock_timeout='5s'; SET LOCAL statement_timeout='30s'; SELECT pg_advisory_xact_lock(hashtext('h4u-schema-migrations'));"


def checksums():
    return {p.name: sha256(p.read_bytes()).hexdigest() for p in sorted(ROOT.glob('*.sql'))
            if '002_' <= p.name <= NAME}


def literal(value):
    return "'" + value.replace("'", "''") + "'"


def migration_sql(ledger, demo=False):
    expected = checksums()
    if {k:v for k,v in ledger.items() if k != NAME} != {k:v for k,v in expected.items() if k != NAME}:
        raise RuntimeError('012 prerequisite checksum mismatch')
    if NAME in ledger and ledger[NAME] != expected[NAME]:
        raise RuntimeError('012 checksum mismatch')
    guard = "DO $$ BEGIN IF (SELECT jsonb_object_agg(version,checksum) FROM schema_migrations) IS DISTINCT FROM " + literal(json.dumps(ledger)) + "::jsonb THEN RAISE EXCEPTION '012 ledger changed'; END IF; END $$;"
    ddl = '' if NAME in ledger else (ROOT/NAME).read_text() + '\nINSERT INTO schema_migrations(version,checksum) VALUES (' + literal(NAME) + ',' + literal(expected[NAME]) + ');'
    permissions = ('GRANT SELECT ON experience_products TO h4u_demo_app; REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON experience_products FROM h4u_demo_app;' if demo else '')
    return LOCKS + guard + ddl + permissions + """DO $$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='experience_products'::regclass
        AND conname='experience_products_product_destination_fk' AND convalidated AND confdeltype='r'
        AND pg_get_constraintdef(oid)='FOREIGN KEY (product_id, destination_id) REFERENCES products(id, destination_id) ON DELETE RESTRICT')
        OR NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='experience_products'::regclass
        AND conname='experience_products_unique' AND convalidated
        AND pg_get_constraintdef(oid)='UNIQUE (destination_id, experience_key, product_id)') THEN
        RAISE EXCEPTION '012 constraints mismatch'; END IF;
      END $$;"""


def apply(conn):
    ledger = dict(conn.execute('SELECT version,checksum FROM schema_migrations').fetchall())
    conn.execute(migration_sql(ledger))


def run_demo(persist=False):
    from scripts.setup_demo import sql, TARGET, check_marker, command, CONTAINER
    if command(['docker','inspect','-f','{{index .Config.Labels "com.h4u.demo"}}',CONTAINER]).strip() != 'isolated':
        raise RuntimeError('Unexpected demo container')
    check_marker()
    ledger = json.loads(sql(TARGET, 'SELECT json_object_agg(version,checksum) FROM schema_migrations'))
    payload = migration_sql(ledger, demo=True)
    sql(TARGET, 'BEGIN;' + payload + 'ROLLBACK;')
    after = json.loads(sql(TARGET, 'SELECT json_object_agg(version,checksum) FROM schema_migrations'))
    if after != ledger:
        raise RuntimeError('012 rollback mismatch')
    if persist:
        sql(TARGET, 'BEGIN;' + payload + 'COMMIT;')
    print(json.dumps({'migration':NAME, 'checksum':checksums()[NAME], 'mode':'apply' if persist else 'dry-run'}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--demo', action='store_true')
    args = parser.parse_args()
    if args.demo:
        run_demo(args.apply)
        return
    from app.db import get_connection
    with get_connection() as conn:
        conn.autocommit = True
        if conn.execute('SELECT current_database()').fetchone() != ('h4u',):
            raise RuntimeError('Use --demo for isolated demo')
        with conn.transaction(force_rollback=True):
            apply(conn)
        if args.apply:
            with conn.transaction():
                apply(conn)
        print(json.dumps({'migration':NAME, 'checksum':checksums()[NAME], 'mode':'apply' if args.apply else 'dry-run'}))


if __name__ == '__main__':
    main()
