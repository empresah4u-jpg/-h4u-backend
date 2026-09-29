"""Official 011 runner: mandatory rollback rehearsal, then explicit --apply."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from psycopg.pq import TransactionStatus

ROOT = Path(__file__).resolve().parents[1] / 'db/migrations'
NAME = '011_ingestion_apply.sql'
TABLES = ('hotels', 'entity_sources', 'entity_source_evidence', 'data_sources', 'entity_embeddings', 'destinations')
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
        'plans',to_regclass('public.ingestion_plans') IS NOT NULL,
        'jobs',to_regclass('public.ingestion_embedding_jobs') IS NOT NULL,
        'objects',(SELECT jsonb_agg(jsonb_build_array(c.relname,c.relacl::text) ORDER BY c.relname)
            FROM pg_class c WHERE c.oid IN (to_regclass('public.ingestion_plans'),to_regclass('public.ingestion_embedding_jobs'))),
        'functions',(SELECT jsonb_agg(jsonb_build_array(proname,prosrc,proacl::text) ORDER BY proname)
            FROM pg_proc WHERE pronamespace='public'::regnamespace AND proname IN
            ('ingestion_plan_guard','ingestion_plan_committed','ingestion_job_guard','lock_ingestion_source_evidence')))::text""" % catalog_sql()


def verify_sql():
    import re
    checks=[]
    for name,body in re.findall(r'CREATE FUNCTION (\w+)\(\) RETURNS .*?AS \$\$(.*?)\$\$;', (ROOT/NAME).read_text(), re.S):
        checks.append("IF NOT EXISTS (SELECT 1 FROM pg_proc WHERE oid="+literal(name+'()')+"::regprocedure AND prosrc="+literal(body)+" AND pronargs=0 AND proowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) AND prosecdef="+('true' if name=='lock_ingestion_source_evidence' else 'false')+") THEN RAISE EXCEPTION '011 function/owner mismatch'; END IF;")
    if len(checks)!=4:
        raise RuntimeError('Unexpected 011 function definitions')
    return "DO $verify011$ BEGIN " + ''.join(checks) + """
    IF (SELECT proconfig FROM pg_proc WHERE oid='lock_ingestion_source_evidence()'::regprocedure)
        IS DISTINCT FROM ARRAY['search_path=pg_catalog, pg_temp']::text[] THEN
        RAISE EXCEPTION 'Unsafe lock function search_path'; END IF;
    IF EXISTS (SELECT 1 FROM pg_proc p CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
        WHERE p.oid='lock_ingestion_source_evidence()'::regprocedure AND a.grantee=0) THEN
        RAISE EXCEPTION 'PUBLIC function privilege'; END IF;
    IF (SELECT count(*) FROM pg_constraint WHERE conrelid='ingestion_plans'::regclass AND contype='c' AND convalidated)<>8
       OR (SELECT count(*) FROM pg_constraint WHERE conrelid='ingestion_embedding_jobs'::regclass AND contype='c' AND convalidated)<>8 THEN
        RAISE EXCEPTION '011 state constraints mismatch'; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='ingestion_plans'::regclass AND contype='u'
        AND pg_get_constraintdef(oid)='UNIQUE (plan_fingerprint)')
       OR NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='ingestion_embedding_jobs'::regclass AND contype='u'
        AND pg_get_constraintdef(oid)='UNIQUE (ingestion_plan_id, entity_type, entity_id, content_hash)') THEN
        RAISE EXCEPTION '011 dedup mismatch'; END IF;
    IF (SELECT count(*) FROM pg_constraint WHERE conrelid='ingestion_embedding_jobs'::regclass AND contype='f' AND confdeltype='r'
        AND convalidated AND confrelid IN ('ingestion_plans'::regclass,'hotels'::regclass))<>2 THEN
        RAISE EXCEPTION '011 FK mismatch'; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid='ingestion_plans'::regclass
        AND tgname='ingestion_plan_finished' AND tgdeferrable AND tginitdeferred AND tgenabled='O')
        OR NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid='ingestion_plans'::regclass
        AND tgname='ingestion_plan_immutable' AND tgenabled='O')
        OR NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid='ingestion_embedding_jobs'::regclass
        AND tgname='ingestion_job_transition' AND tgenabled='O') THEN
        RAISE EXCEPTION '011 guard mismatch'; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_index WHERE indexrelid=to_regclass('ingestion_embedding_jobs_ready') AND indisvalid)
        OR EXISTS (SELECT 1 FROM pg_index WHERE indrelid IN ('ingestion_plans'::regclass,'ingestion_embedding_jobs'::regclass) AND NOT indisvalid) THEN
        RAISE EXCEPTION '011 index mismatch'; END IF;
    END $verify011$;"""


def runtime_grants(role):
    # Role is an installer-controlled identifier; quote it, never interpolate caller SQL.
    name='"'+role.replace('"','""')+'"'
    return (
        f'REVOKE ALL ON ingestion_plans,ingestion_embedding_jobs FROM {name};'
        f'GRANT SELECT,INSERT ON ingestion_plans,ingestion_embedding_jobs TO {name};'
        f'GRANT UPDATE(status,result,applied_at) ON ingestion_plans TO {name};'
        f'GRANT UPDATE(status,attempts,last_error,updated_at,completed_at) ON ingestion_embedding_jobs TO {name};'
        f'GRANT EXECUTE ON FUNCTION lock_ingestion_source_evidence() TO {name};'
    )


def runtime_verify(role):
    who=literal(role)
    return "DO $runtime011$ BEGIN " +         f"IF NOT has_function_privilege({who},'lock_ingestion_source_evidence()','EXECUTE') OR has_schema_privilege({who},'public','CREATE') THEN RAISE EXCEPTION '011 runtime privilege mismatch'; END IF;" +         ''.join(f"IF has_table_privilege({who},'entity_source_evidence','{priv}') THEN RAISE EXCEPTION 'Excessive evidence privilege'; END IF;" for priv in ('UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER')) +         "END $runtime011$;"


def integrity_sql(before):
    return "DO $integrity011$ BEGIN IF " + catalog_sql() + ' <> ' + literal(json.dumps(before)) + "::jsonb THEN RAISE EXCEPTION 'Catalog changed during 011'; END IF; END $integrity011$;"


def migration_sql(ledger, demo=False):
    expected = checksums()
    previous = {k: v for k, v in expected.items() if k != NAME}
    if {k:v for k,v in ledger.items() if k != NAME} != previous:
        raise RuntimeError('011 prerequisite checksum mismatch')
    if NAME in ledger and ledger[NAME] != expected[NAME]:
        raise RuntimeError('011 checksum mismatch')
    # Recheck under the advisory lock; refuse any concurrent registry drift.
    check = "DO $ledger011$ BEGIN IF (SELECT jsonb_object_agg(version,checksum) FROM schema_migrations) IS DISTINCT FROM " + literal(json.dumps(ledger)) + "::jsonb THEN RAISE EXCEPTION 'Migration registry changed'; END IF; END $ledger011$;"
    ddl = '' if NAME in ledger else (ROOT/NAME).read_text() + '\nINSERT INTO schema_migrations(version,checksum) VALUES (' + literal(NAME) + ',' + literal(expected[NAME]) + ');'
    permissions = runtime_grants('h4u_demo_app') + runtime_verify('h4u_demo_app') if demo else ''

    return check + ddl + permissions + verify_sql() + ("DO $$ BEGIN IF EXISTS(SELECT 1 FROM ingestion_plans) OR EXISTS(SELECT 1 FROM ingestion_embedding_jobs) THEN RAISE EXCEPTION 'Unexpected initial ingestion rows'; END IF; END $$;" if NAME not in ledger else '')


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
        raise RuntimeError('011 demo rollback mismatch')
    if persist:
        sql(TARGET, 'BEGIN; SET LOCAL search_path=public,pg_catalog; ' + payload + 'COMMIT;')
        after = report()
        if after['catalog'] != before['catalog'] or after['ledger'] != checksums():
            raise RuntimeError('011 demo post-application mismatch')
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
            raise RuntimeError('011 rollback mismatch')
        if not rollback and (after['catalog'] != before['catalog'] or after['ledger'] != checksums()):
            raise RuntimeError('011 post-application mismatch')
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
    from app.db import get_connection
    with get_connection() as conn:
        conn.autocommit=True
        run(conn,args.apply)


if __name__ == '__main__':
    main()
