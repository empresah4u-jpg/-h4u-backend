"""Official 010 runner: mandatory rollback rehearsal, then explicit --apply."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from psycopg.pq import TransactionStatus

ROOT = Path(__file__).resolve().parents[1] / 'db/migrations'
NAME = '010_entity_source_evidence.sql'
TABLES = ('hotels', 'entity_sources', 'data_sources', 'entity_embeddings', 'destinations')
LOCKS = "SET LOCAL lock_timeout='5s'; SET LOCAL statement_timeout='30s'; SELECT pg_advisory_xact_lock(hashtext('h4u-schema-migrations')); LOCK TABLE " + ','.join(TABLES) + ' IN SHARE MODE;'


def literal(value):
    return "'" + value.replace("'", "''") + "'"


def checksums():
    return {p.name: sha256(p.read_bytes()).hexdigest() for p in sorted(ROOT.glob('*.sql'))
            if '002_' <= p.name <= NAME}


def catalog_sql():
    return 'jsonb_build_object(' + ','.join(
        literal(t) + ", (SELECT jsonb_build_array(count(*),md5(coalesce(string_agg(to_jsonb(t)::text,'' ORDER BY id),''))) FROM " + t + ' t)'
        for t in TABLES) + ')'


def report_sql():
    return """SELECT jsonb_build_object('catalog', %s,
        'ledger',(SELECT jsonb_object_agg(version,checksum) FROM schema_migrations),
        'table_exists',to_regclass('public.entity_source_evidence') IS NOT NULL,
        'function_exists',to_regprocedure('public.entity_source_evidence_guard()') IS NOT NULL,
        'acl',(SELECT relacl::text FROM pg_class WHERE oid=to_regclass('public.entity_source_evidence')),
        'constraints',(SELECT jsonb_agg(pg_get_constraintdef(oid) ORDER BY conname) FROM pg_constraint
            WHERE conrelid=to_regclass('public.entity_source_evidence')),
        'triggers',(SELECT jsonb_agg(pg_get_triggerdef(oid) ORDER BY tgname) FROM pg_trigger
            WHERE tgrelid=to_regclass('public.entity_source_evidence') AND NOT tgisinternal))::text""" % catalog_sql()


def verify_sql():
    body = (ROOT/NAME).read_text().split('AS $$', 1)[1].split('$$;', 1)[0]
    return """DO $verify010$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='entity_sources'::regclass
        AND conname='entity_sources_source_entity_unique' AND convalidated
        AND pg_get_constraintdef(oid)='UNIQUE (source_id, entity_type, entity_id)') THEN
        RAISE EXCEPTION 'Parent UNIQUE mismatch'; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='entity_source_evidence'::regclass
        AND contype='f' AND confrelid='entity_sources'::regclass AND confdeltype='r' AND convalidated
        AND pg_get_constraintdef(oid)='FOREIGN KEY (entity_source_id) REFERENCES entity_sources(id) ON DELETE RESTRICT') THEN
        RAISE EXCEPTION 'Evidence FK mismatch'; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='entity_source_evidence'::regclass
        AND conname='entity_source_evidence_dedup' AND convalidated
        AND pg_get_constraintdef(oid)='UNIQUE (entity_source_id, fingerprint)') THEN
        RAISE EXCEPTION 'Evidence UNIQUE mismatch'; END IF;
    IF EXISTS (SELECT 1 FROM pg_index WHERE indrelid='entity_source_evidence'::regclass AND NOT indisvalid) THEN
        RAISE EXCEPTION 'Invalid evidence index'; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid='entity_source_evidence'::regclass
        AND tgname='entity_source_evidence_immutable' AND tgenabled='O' AND tgtype=31
        AND tgfoid='entity_source_evidence_guard()'::regprocedure) THEN
        RAISE EXCEPTION 'Immutable trigger mismatch'; END IF;
    IF (SELECT prosrc FROM pg_proc WHERE oid='entity_source_evidence_guard()'::regprocedure) IS DISTINCT FROM %s THEN
        RAISE EXCEPTION 'Fingerprint/immutability function mismatch'; END IF;
    END $verify010$;""" % literal(body)


def integrity_sql(before):
    return "DO $integrity010$ BEGIN IF " + catalog_sql() + ' <> ' + literal(json.dumps(before)) + "::jsonb THEN RAISE EXCEPTION 'Catalog changed during 010'; END IF; END $integrity010$;"


def migration_sql(ledger, demo=False):
    expected = checksums()
    previous = {k: v for k, v in expected.items() if k != NAME}
    if {k:v for k,v in ledger.items() if k != NAME} != previous:
        raise RuntimeError('010 prerequisite checksum mismatch')
    if NAME in ledger and ledger[NAME] != expected[NAME]:
        raise RuntimeError('010 checksum mismatch')
    # Recheck under the advisory lock; refuse any concurrent registry drift.
    check = "DO $ledger010$ BEGIN IF (SELECT jsonb_object_agg(version,checksum) FROM schema_migrations) IS DISTINCT FROM " + literal(json.dumps(ledger)) + "::jsonb THEN RAISE EXCEPTION 'Migration registry changed'; END IF; END $ledger010$;"
    ddl = '' if NAME in ledger else (ROOT/NAME).read_text() + '\nINSERT INTO schema_migrations(version,checksum) VALUES (' + literal(NAME) + ',' + literal(expected[NAME]) + ');'
    permissions = ('GRANT SELECT,INSERT ON entity_source_evidence TO h4u_demo_app;'
                   'REVOKE UPDATE,DELETE,TRUNCATE ON entity_source_evidence FROM h4u_demo_app;'
                   "DO $$ BEGIN IF NOT has_table_privilege('h4u_demo_app','entity_source_evidence','SELECT') OR NOT has_table_privilege('h4u_demo_app','entity_source_evidence','INSERT') OR has_table_privilege('h4u_demo_app','entity_source_evidence','UPDATE') OR has_table_privilege('h4u_demo_app','entity_source_evidence','DELETE') OR has_table_privilege('h4u_demo_app','entity_source_evidence','TRUNCATE') THEN RAISE EXCEPTION 'Demo evidence privileges mismatch'; END IF; END $$;"
                   if demo else "DO $$ BEGIN IF NOT has_table_privilege(current_user,'entity_source_evidence','SELECT') OR NOT has_table_privilege(current_user,'entity_source_evidence','INSERT') THEN RAISE EXCEPTION 'Evidence privileges missing'; END IF; END $$;")
    return check + ddl + permissions + verify_sql()


def run_demo(persist=False):
    from scripts.setup_demo import sql, command, CONTAINER, TARGET, check_marker
    if command(['docker','inspect','-f','{{index .Config.Labels "com.h4u.demo"}}',CONTAINER]).strip() != 'isolated':
        raise RuntimeError('Unexpected demo container')
    check_marker()
    def report():
        output = sql(TARGET, 'BEGIN READ ONLY; ' + report_sql() + '; COMMIT;')
        return json.loads(next(line for line in output.splitlines() if line.startswith('{')))
    before = report()
    payload = LOCKS + migration_sql(before['ledger'], demo=True) + integrity_sql(before['catalog'])
    sql(TARGET, 'BEGIN; SET LOCAL search_path=public,pg_catalog; ' + payload + 'ROLLBACK;')
    if report() != before:
        raise RuntimeError('010 demo rollback mismatch')
    if persist:
        sql(TARGET, 'BEGIN; SET LOCAL search_path=public,pg_catalog; ' + payload + 'COMMIT;')
        after = report()
        if after['catalog'] != before['catalog'] or after['ledger'] != checksums():
            raise RuntimeError('010 demo post-application mismatch')
    print(json.dumps({'database':TARGET,'mode':'apply' if persist else 'dry-run',
                      'checksum':checksums()[NAME],'rollback_verified':True,'catalog_unchanged':True,
                      'catalog':before['catalog']}))


def run(conn, persist=False):
    if not conn.autocommit or conn.info.transaction_status != TransactionStatus.IDLE:
        raise RuntimeError('Requires idle autocommit connection')
    if conn.execute('SELECT current_database()').fetchone() != ('h4u',):
        raise RuntimeError('Use --demo for isolated demo; normal target must be h4u')
    def report():
        with conn.transaction():
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            return json.loads(conn.execute(report_sql()).fetchone()[0])
    before = report()
    payload = migration_sql(before['ledger'])
    for rollback in ([True, False] if persist else [True]):
        with conn.transaction(force_rollback=rollback):
            conn.execute('SET LOCAL search_path=public,pg_catalog')
            conn.execute(LOCKS)
            conn.execute(payload)
            conn.execute(integrity_sql(before['catalog']))
        after = report()
        if rollback and after != before:
            raise RuntimeError('010 rollback mismatch')
        if not rollback and (after['catalog'] != before['catalog'] or after['ledger'] != checksums()):
            raise RuntimeError('010 post-application mismatch')
    print(json.dumps({'database':'h4u','mode':'apply' if persist else 'dry-run',
                      'checksum':checksums()[NAME],'rollback_verified':True,'catalog_unchanged':True,
                      'catalog':before['catalog']}))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--demo',action='store_true')
    args=parser.parse_args()
    if args.demo:
        # Same reviewed upgrade entry point used by existing official runners.
        if args.apply:
            from scripts.setup_demo import upgrade_demo_schema, expected_migrations
            upgrade_demo_schema(expected_migrations())
        else:
            run_demo()
        return
    from app.db import get_admin_connection as get_connection
    with get_connection() as conn:
        conn.autocommit=True
        run(conn,args.apply)


if __name__ == '__main__':
    main()
