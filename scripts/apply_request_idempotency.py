"""014 installer: ONLY disposable databases authorized in this checkpoint."""
from pathlib import Path
from hashlib import sha256

NAME = '014_service_request_idempotency.sql'
FILE = Path(__file__).resolve().parents[1] / 'db/migrations' / NAME


def apply(conn, runtime_role):
    if not conn.execute('SELECT current_database()').fetchone()[0].startswith('h4u_ingestion_test_'):
        raise RuntimeError('014 is authorized ONLY in disposable databases')
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('h4u-schema-migrations'))")
    digest = sha256(FILE.read_bytes()).hexdigest()
    previous = conn.execute('SELECT checksum FROM schema_migrations WHERE version=%s',(NAME,)).fetchone()
    if previous:
        if previous[0] != digest:
            raise RuntimeError('014 checksum mismatch')
        return
    conn.execute("SELECT set_config('h4u.idempotency_runtime_role',%s,true)",(runtime_role,))
    conn.execute(FILE.read_text())
    conn.execute('INSERT INTO schema_migrations(version,checksum) VALUES (%s,%s)',(NAME,digest))


APPROVED_CHECKSUM = 'fe003c2a4509e940d71cd8bf9dcbf0fe87e5b8f4870e4551f4f51ea23a037aa4'
LOCAL_IDENTITIES = {'demo': ('h4u_demo','h4u_demo_setup','h4u_demo_app','7689633682496192550'),
                    'h4u-local': ('h4u','h4u','h4u_runtime','7676591420387823655')}


def controlled(target, confirmation, persist=False):
    """Pinned local Docker cluster only; default is a rollback rehearsal."""
    import json
    from scripts.setup_demo import sql, command, check_marker
    from scripts.apply_offer_consent import literal
    if target not in LOCAL_IDENTITIES or confirmation != APPROVED_CHECKSUM or sha256(FILE.read_bytes()).hexdigest()!=APPROVED_CHECKSUM:
        raise RuntimeError('Explicit target and exact approved checksum required')
    db,admin,runtime,system_id=LOCAL_IDENTITIES[target]
    legacy=target=='h4u-local'
    container='h4u-postgres' if legacy else 'h4u-demo-postgres'
    context=json.loads(command(['docker','context','inspect']))[0]['Endpoints']['docker']['Host']
    if not context.startswith('unix://'):raise RuntimeError('Local Docker socket required')
    if not legacy:
        if command(['docker','inspect','-f','{{index .Config.Labels "com.h4u.demo"}}',container]).strip()!='isolated':raise RuntimeError('Demo isolation missing')
        check_marker()
    else:
        from app.db import get_connection
        with get_connection() as conn:
            if conn.info.host not in ('127.0.0.1','localhost') or conn.info.port!=5432:
                raise RuntimeError('Local runtime connection required')
            if conn.execute('SELECT current_database(),current_user').fetchone()!=('h4u','h4u_runtime'):
                raise RuntimeError('Unexpected application runtime')
    def query(text):return sql(db,text,legacy=legacy)
    identity=query("SELECT current_database()||'|'||current_user||'|'||(SELECT system_identifier::text FROM pg_control_system())")
    if identity!='|'.join((db,admin,system_id)):raise RuntimeError('Local server/migrator identity mismatch')
    flags=query("SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolbypassrls OR rolreplication FROM pg_roles WHERE rolname="+literal(runtime))
    if flags!='f':raise RuntimeError('Runtime must be restricted')
    expected={p.name:sha256(p.read_bytes()).hexdigest() for p in sorted(FILE.parent.glob('*.sql')) if '002_'<=p.name<NAME}
    ledger=json.loads(query('SELECT json_object_agg(version,checksum) FROM schema_migrations'))
    if ledger!=expected:raise RuntimeError('Prerequisite ledger mismatch or 014 already present')
    if query("SELECT to_regclass('public.service_request_idempotency') IS NULL")!='t':raise RuntimeError('Unexpected existing 014 table')
    tables=json.loads(query("SELECT json_agg(tablename ORDER BY tablename) FROM pg_tables WHERE schemaname='public' AND tablename <> 'schema_migrations'"))
    identifier=lambda name:'"'+name.replace('"','""')+'"'
    snapshot="SELECT jsonb_object_agg(name,fingerprint) FROM ("+' UNION ALL '.join("SELECT "+literal(t)+" AS name,jsonb_build_array(count(*),md5(coalesce(string_agg(to_jsonb(x)::text,'' ORDER BY to_jsonb(x)::text),''))) AS fingerprint FROM "+identifier(t)+' x' for t in tables)+') AS fingerprints'
    before=query(snapshot)
    product_state=None
    if legacy:
        product_state=json.loads(query("SELECT json_agg(json_build_object('code',p.code,'status',p.status,'enabled',p.reservations_enabled,'partners',(SELECT count(*) FROM product_partners pp WHERE pp.product_id=p.id))) FROM products p WHERE code IN ('PROD-BALLESTAS','PROD-TOUR025')"))
        ballestas=next((p for p in product_state if p['code']=='PROD-BALLESTAS'),None)
        if ballestas!={'code':'PROD-BALLESTAS','status':'draft','enabled':False,'partners':0} or len(product_state)!=2:raise RuntimeError('Protected product precheck failed')
    guard="DO $$ BEGIN IF ("+snapshot+") IS DISTINCT FROM "+literal(before)+"::jsonb THEN RAISE EXCEPTION 'Historical fingerprint changed'; END IF; END $$;"
    grant_extra=("GRANT INSERT ON sessions TO h4u_runtime; GRANT UPDATE(id) ON destinations TO h4u_runtime;" if legacy else '')
    checks="""DO $$ BEGIN
      IF (SELECT count(*) FROM service_request_idempotency)<>0 THEN RAISE EXCEPTION 'Unexpected new rows'; END IF;
      IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='service_request_idempotency'::regclass AND tgname='immutable_service_request_attempt' AND tgenabled='O') THEN RAISE EXCEPTION 'Missing immutable trigger'; END IF;
      IF (SELECT count(*) FROM pg_constraint WHERE conrelid='service_request_idempotency'::regclass AND contype='f' AND confdeltype='r')<>2 THEN RAISE EXCEPTION 'Missing RESTRICT FKs'; END IF;
      IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='service_request_idempotency'::regclass AND contype='u') THEN RAISE EXCEPTION 'Missing UNIQUE'; END IF;
      IF NOT has_table_privilege(ROLE,'service_request_idempotency','SELECT') OR NOT has_table_privilege(ROLE,'service_request_idempotency','INSERT') OR has_table_privilege(ROLE,'service_request_idempotency','UPDATE,DELETE,TRUNCATE') OR has_schema_privilege(ROLE,'public','CREATE') THEN RAISE EXCEPTION 'Runtime privileges mismatch'; END IF;
      IF (SELECT pg_get_userbyid(relowner)=ROLE FROM pg_class WHERE oid='service_request_idempotency'::regclass) THEN RAISE EXCEPTION 'Runtime ownership'; END IF;
      END $$;""".replace('ROLE',literal(runtime))
    payload="BEGIN; SET LOCAL lock_timeout='5s'; SET LOCAL statement_timeout='60s'; SELECT pg_advisory_xact_lock(hashtext('h4u-schema-migrations')); LOCK TABLE "+','.join(identifier(t) for t in tables)+",schema_migrations IN SHARE ROW EXCLUSIVE MODE;"+guard
    payload+="DO $$ BEGIN IF (SELECT jsonb_object_agg(version,checksum) FROM schema_migrations) IS DISTINCT FROM "+literal(json.dumps(ledger))+"::jsonb THEN RAISE EXCEPTION 'Ledger changed'; END IF; END $$;"
    payload+="SELECT set_config('h4u.idempotency_runtime_role',"+literal(runtime)+",true);"+FILE.read_text()+grant_extra
    payload+="INSERT INTO schema_migrations(version,checksum) VALUES ("+literal(NAME)+","+literal(APPROVED_CHECKSUM)+");"+checks+guard
    query(payload+'ROLLBACK;')
    if query(snapshot)!=before or json.loads(query('SELECT json_object_agg(version,checksum) FROM schema_migrations'))!=ledger:raise RuntimeError('Rollback verification failed')
    if persist:
        query(payload+'COMMIT;')
        if query(snapshot)!=before:raise RuntimeError('POST historical fingerprint mismatch')
        expected[NAME]=APPROVED_CHECKSUM
        if json.loads(query('SELECT json_object_agg(version,checksum) FROM schema_migrations'))!=expected:raise RuntimeError('POST ledger mismatch')
    print(json.dumps({'target':target,'mode':'apply' if persist else 'rollback','checksum':APPROVED_CHECKSUM,'server_identity_verified':True,'runtime':runtime,'fingerprints_identical':True,'historical_tables':json.loads(before),'products':product_state},sort_keys=True))


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--target',required=True,choices=LOCAL_IDENTITIES)
    parser.add_argument('--confirm-checksum',required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    controlled(args.target,args.confirm_checksum,args.apply)
