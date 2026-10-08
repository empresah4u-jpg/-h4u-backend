"""013 official runner. Defaults to rollback rehearsal; persistence requires --apply."""
import argparse
from hashlib import sha256
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1] / 'db/migrations'
NAME = '013_offer_consent.sql'
LOCKS = "SET LOCAL lock_timeout='5s'; SET LOCAL statement_timeout='30s'; SELECT pg_advisory_xact_lock(hashtext('h4u-schema-migrations'));"


def checksums():
    return {p.name: sha256(p.read_bytes()).hexdigest() for p in sorted(ROOT.glob('*.sql'))
            if '002_' <= p.name <= NAME}


def literal(value):
    return "'" + value.replace("'", "''") + "'"


def migration_sql(ledger, demo=False):
    expected = checksums()
    if {k:v for k,v in ledger.items() if k != NAME} != {k:v for k,v in expected.items() if k != NAME}:
        raise RuntimeError('013 prerequisite checksum mismatch')
    if NAME in ledger and ledger[NAME] != expected[NAME]:
        raise RuntimeError('013 checksum mismatch')
    guard = "DO $$ BEGIN IF (SELECT jsonb_object_agg(version,checksum) FROM schema_migrations) IS DISTINCT FROM " + literal(json.dumps(ledger)) + "::jsonb THEN RAISE EXCEPTION '013 ledger changed'; END IF; END $$;"
    ddl = '' if NAME in ledger else (ROOT/NAME).read_text() + '\nINSERT INTO schema_migrations(version,checksum) VALUES (' + literal(NAME) + ',' + literal(expected[NAME]) + ');'
    permissions = ('GRANT SELECT,INSERT ON request_offer_consents TO h4u_demo_app; REVOKE UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON request_offer_consents FROM h4u_demo_app;' if demo else '')
    return LOCKS + guard + ddl + permissions + """DO $$ BEGIN
      IF to_regclass('request_offer_consents') IS NULL OR NOT EXISTS(
        SELECT 1 FROM pg_trigger WHERE tgrelid='request_offer_consents'::regclass
        AND tgname='consent_immutable' AND tgenabled='O') THEN
        RAISE EXCEPTION '013 consent guard missing'; END IF;
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
    tables=('products','product_partners','partners','tours','service_requests','request_partners',
            'reservations','payments','commissions','partner_settlements','refunds')
    def fingerprints():
        pairs=[]
        for table in tables:
            drop={'service_requests':['terms_revision'],'request_partners':['offer_version','offer_request_revision','offer_request_snapshot'],'reservations':['consent_id']}.get(table,[])
            expr='to_jsonb(x)'+''.join('-'+literal(column) for column in drop)
            pairs.append(literal(table)+",(SELECT jsonb_build_array(count(*),md5(coalesce(string_agg(("+expr+")::text,'' ORDER BY id),''))) FROM "+table+' x)')
        return sql(TARGET,"SELECT jsonb_build_object("+','.join(pairs)+")")
    before=fingerprints()
    payload = migration_sql(ledger, demo=True)
    sql(TARGET, 'BEGIN;' + payload + 'ROLLBACK;')
    after = json.loads(sql(TARGET, 'SELECT json_object_agg(version,checksum) FROM schema_migrations'))
    if after != ledger or fingerprints()!=before:
        raise RuntimeError('013 rollback mismatch')
    if persist:
        sql(TARGET, 'BEGIN;' + payload + 'COMMIT;')
        if fingerprints()!=before:
            raise RuntimeError('013 historical fingerprint mismatch')
        if json.loads(sql(TARGET, 'SELECT json_object_agg(version,checksum) FROM schema_migrations'))!=checksums():
            raise RuntimeError('013 ledger mismatch')
    print(json.dumps({'migration':NAME, 'checksum':checksums()[NAME], 'mode':'apply' if persist else 'dry-run','historical_fingerprints_identical':True}))


H4U_CONFIRMATION = '434237acaaed3d5127476918a55b6a3d2671717f53460be901128b492d4d30cf'
H4U_TABLES = ('products','partners','product_partners','service_requests','request_partners',
    'reservations','payments','refunds','commissions','partner_settlements','tours','hotels',
    'restaurants','attractions','entity_sources','entity_embeddings','destinations')


def historical_fingerprints(conn):
    drops={'service_requests':['terms_revision'],'request_partners':['offer_version','offer_request_revision','offer_request_snapshot'],'reservations':['consent_id']}
    result={}
    for table in H4U_TABLES:
        expr='to_jsonb(x)'+''.join('-'+literal(c) for c in drops.get(table,[]))
        result[table]=conn.execute("SELECT count(*),md5(coalesce(string_agg(("+expr+")::text,'' ORDER BY id),'')) FROM "+table+' x').fetchone()
    return result


def run_h4u(confirmation, persist=False):
    if confirmation!=H4U_CONFIRMATION or checksums()[NAME]!=H4U_CONFIRMATION:
        raise RuntimeError('Explicit H4U authorization with exact 013 checksum required')
    from app.db import get_admin_connection, get_connection
    from scripts.setup_runtime import grants
    with get_connection() as runtime:
        if runtime.execute('SELECT current_database(),current_user').fetchone()!=('h4u','h4u_runtime'):
            raise RuntimeError('Restricted H4U runtime must be active before migration')
    with get_admin_connection() as conn:
        conn.autocommit=True
        if conn.execute('SELECT current_database(),current_user').fetchone()!=('h4u','h4u'):
            raise RuntimeError('Expected separate H4U migrator')
        before=historical_fingerprints(conn)
        ledger=dict(conn.execute('SELECT version,checksum FROM schema_migrations').fetchall())
        for rollback in ([True,False] if persist else [True]):
            with conn.transaction(force_rollback=rollback):
                conn.execute(LOCKS)
                conn.execute('LOCK TABLE '+','.join(H4U_TABLES)+' IN SHARE ROW EXCLUSIVE MODE')
                if historical_fingerprints(conn)!=before:raise RuntimeError('Concurrent historical change; stop')
                apply(conn)
                grants(conn)
                if historical_fingerprints(conn)!=before:raise RuntimeError('Historical data changed; rollback')
                if conn.execute('SELECT count(*) FROM request_offer_consents').fetchone()!=(0,):
                    raise RuntimeError('Unexpected consent evidence; stop')
            if rollback and dict(conn.execute('SELECT version,checksum FROM schema_migrations').fetchall())!=ledger:
                raise RuntimeError('Rollback ledger mismatch')
        if historical_fingerprints(conn)!=before:raise RuntimeError('POST fingerprint mismatch')
        print(json.dumps({'migration':NAME,'checksum':H4U_CONFIRMATION,'mode':'apply' if persist else 'dry-run',
            'fingerprints_identical':True,'tables':before},default=str))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--confirm-h4u', help='Exact approved 013 SHA-256; required for H4U')
    args = parser.parse_args()
    if args.demo:
        run_demo(args.apply)
        return
    if not args.confirm_h4u:
        raise SystemExit('H4U requires --confirm-h4u with the approved checksum')
    try:
        run_h4u(args.confirm_h4u, args.apply)
    except Exception as exc:
        raise SystemExit('013 H4U deployment stopped: '+type(exc).__name__) from None


if __name__ == '__main__':
    main()
