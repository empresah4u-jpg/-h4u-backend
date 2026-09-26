"""Administrative setup for an isolated demo cluster; runtime never receives admin credentials."""
import argparse
from datetime import datetime,timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import subprocess
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]
CONTAINER='h4u-demo-postgres'
LEGACY_CONTAINER='h4u-postgres'
SOURCE='h4u'
TARGET='h4u_demo'
ROLE='h4u_demo_app'
ADMIN='h4u_demo_setup'


def command(args,input=None,binary=False):
    result=subprocess.run(args,input=input,text=not binary,capture_output=True,timeout=180)
    if result.returncode: raise RuntimeError('Demo administrative command failed; output withheld for privacy')
    return result.stdout


def docker(args,input=None,legacy=False,binary=False):
    return command(['docker','exec','-i',LEGACY_CONTAINER if legacy else CONTAINER,*args],input,binary)


def sql(database,query,legacy=False):
    return docker(['psql','-X','-U','h4u' if legacy else ADMIN,'-d',database,'-At','-v','ON_ERROR_STOP=1'],
                  input=query,legacy=legacy).strip()


def expected_migrations():
    return {p.name:sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'db/migrations').glob('*.sql')) if not p.name.startswith('001_')}


def validate_target(database,rebuild=False,confirmation=None):
    if database!=TARGET: raise RuntimeError('Only the exact h4u_demo target is permitted')
    if rebuild and confirmation!=TARGET: raise RuntimeError('Rebuild requires --confirm h4u_demo')


def write_private(path,value):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream: stream.write(value)


def local_config():
    private=ROOT/'.demo-private'; private.mkdir(mode=0o700,exist_ok=True)
    admin_path=private/'admin_password'
    if not admin_path.exists(): write_private(admin_path,secrets.token_urlsafe(48))
    env_path=ROOT/'.env.demo'
    if not env_path.exists():
        write_private(env_path,'DEMO_DB_HOST=127.0.0.1\nDEMO_DB_PORT=55432\nDEMO_DB_NAME=h4u_demo\nDEMO_DB_USER=h4u_demo_app\nDEMO_DB_PASSWORD='+secrets.token_urlsafe(48)+'\n')
    values=dotenv_values(env_path,interpolate=False)
    password=values.get('DEMO_DB_PASSWORD','')
    if len(password)<32 or not all(c.isalnum() or c in '_-' for c in password):
        raise RuntimeError('Invalid local demo credential configuration')
    return password


def check_marker():
    if sql(TARGET,"SELECT to_regclass('public.demo_environment') IS NOT NULL")!='t':
        raise RuntimeError('Existing target is not marked as demo')
    if sql(TARGET,'SELECT purpose FROM public.demo_environment WHERE singleton')!='h4u-persistent-demo':
        raise RuntimeError('Demo marker mismatch')


def configure_runtime(password):
    # Setup-only role commands. Never expose SQL containing the generated password.
    sql('postgres',"SET log_statement='none'; SET log_min_error_statement='panic'; DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='h4u_demo_app') THEN CREATE ROLE h4u_demo_app LOGIN; END IF; END $$; ALTER ROLE h4u_demo_app NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT PASSWORD '"+password+"'; REVOKE ALL ON DATABASE postgres,template0,template1 FROM PUBLIC;")
    sql(TARGET,'''REVOKE ALL ON DATABASE h4u_demo FROM PUBLIC;
        GRANT CONNECT ON DATABASE h4u_demo TO h4u_demo_app;
        REVOKE CREATE ON SCHEMA public FROM PUBLIC;
        GRANT USAGE ON SCHEMA public TO h4u_demo_app;
        GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA public TO h4u_demo_app;
        GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO h4u_demo_app;
        REVOKE ALL ON demo_environment,schema_migrations FROM h4u_demo_app;
        GRANT SELECT ON demo_environment,schema_migrations TO h4u_demo_app;''')


def upgrade_demo_schema(expected):
    """Only explicitly reviewed incremental migrations; same files as normal DB."""
    check_marker()
    current=json.loads(sql(TARGET,'SELECT json_object_agg(version,checksum) FROM schema_migrations'))
    if any(expected.get(k)!=v for k,v in current.items()):
        raise RuntimeError('Demo migration checksum mismatch')
    missing=sorted(set(expected)-set(current))
    if not missing: return
    if missing!=['008_commercial_lifecycle.sql']:
        raise RuntimeError('No reviewed incremental upgrade for this schema')
    source=(ROOT/'db/migrations'/missing[0]).read_text()
    registration="INSERT INTO schema_migrations(version,checksum) VALUES ('"+missing[0]+"','"+expected[missing[0]]+"');"
    guard="SELECT pg_advisory_xact_lock(hashtext('h4u-schema-migrations')); SET LOCAL lock_timeout='5s'; SET LOCAL statement_timeout='30s';"
    before=sql(TARGET,"SELECT to_regclass('public.commercial_slots') IS NULL")
    sql(TARGET,'BEGIN;'+guard+source+registration+'ROLLBACK;')
    if sql(TARGET,"SELECT to_regclass('public.commercial_slots') IS NULL")!=before:
        raise RuntimeError('Demo migration rollback failed')
    sql(TARGET,'BEGIN;'+guard+source+registration+"DO $$ BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='h4u_demo_app') THEN GRANT SELECT,INSERT,UPDATE ON cancellation_policy_versions,commercial_product_settings,cancellation_policy_assignments,commercial_slots,cancellation_cases,refund_commands TO h4u_demo_app; END IF; END $$; COMMIT;")
    if json.loads(sql(TARGET,'SELECT json_object_agg(version,checksum) FROM schema_migrations'))!=expected:
        raise RuntimeError('Demo migration verification failed')


def source_identity_snapshot():
    # Opaque identities only; never copy accounts or credential columns.
    return json.loads(sql(SOURCE,"SELECT json_build_object('users',COALESCE((SELECT json_agg(id) FROM users),'[]'::json),'sessions',COALESCE((SELECT json_agg(id) FROM auth_sessions),'[]'::json))",legacy=True))


def setup(database=TARGET,rebuild=False,confirmation=None):
    validate_target(database,rebuild,confirmation)
    password=local_config()
    command(['docker','compose','-f',str(ROOT/'docker-compose.demo.yml'),'up','-d','--wait','--pull','never'])
    label=command(['docker','inspect','-f','{{index .Config.Labels "com.h4u.demo"}}',CONTAINER]).strip()
    if label!='isolated': raise RuntimeError('Unexpected container; refusing database operations')
    expected=expected_migrations()
    source=json.loads(sql(SOURCE,'SELECT json_object_agg(version,checksum) FROM schema_migrations',legacy=True))
    if source!=expected: raise RuntimeError('Source migration versions do not match reviewed files')
    exists=sql('postgres',"SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname='h4u_demo')")=='t'
    if exists:
        check_marker()
        current=json.loads(sql(TARGET,'SELECT json_object_agg(version,checksum) FROM schema_migrations'))
        if current!=expected: upgrade_demo_schema(expected)
    if rebuild:
        if not exists: raise RuntimeError('Rebuild requires an existing marked demo database')
        # Guarded, explicit DEMO-only destruction; preserve a protected backup first.
        backup=docker(['pg_dump','-U',ADMIN,'-d',TARGET,'-Fc','--no-owner','--no-privileges'],binary=True)
        destination=ROOT/'.demo-private'/('demo-before-rebuild-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.dump')
        fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as stream: stream.write(backup)
        check_marker()
        docker(['dropdb','-U',ADMIN,TARGET])
        exists=False
    if not exists:
        docker(['createdb','-U',ADMIN,'-T','template0',TARGET])
        legacy=not rebuild and sql('postgres',"SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname='h4u_demo')",legacy=True)=='t'
        if legacy:
            if sql(TARGET,"SELECT purpose FROM public.demo_environment WHERE singleton",legacy=True)!='h4u-persistent-demo':
                raise RuntimeError('Legacy demo marker mismatch')
            if sql(TARGET,"SELECT count(*) FROM users WHERE email !~ '^(admin|partner|tourist)\\.[a-f0-9]{12,16}@example\\.invalid$'",legacy=True)!='0':
                raise RuntimeError('Legacy demo contains identities outside the fictional namespace')
            # Preserve only fictional demo data/history; never dump h4u data.
            dump=docker(['pg_dump','-U','h4u','-d',TARGET,'--no-owner','--no-privileges','--schema=public'],legacy=True)
            bootstrap=''
        else:
            dump=docker(['pg_dump','-U','h4u','-d',SOURCE,'--schema-only','--no-owner','--no-privileges','--schema=public'],legacy=True)
            bootstrap=(ROOT/'db/demo/bootstrap.sql').read_text()
            for version,checksum in expected.items():
                if not all(c.isalnum() or c in '._' for c in version): raise RuntimeError('Invalid migration filename')
                bootstrap+="\nINSERT INTO schema_migrations(version,checksum) VALUES ('"+version+"','"+checksum+"');"
        dump=dump.replace('CREATE SCHEMA public;','CREATE SCHEMA IF NOT EXISTS public;')
        payload='CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public; CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;\n'+dump+'\nSET search_path TO public;\n'+bootstrap
        docker(['psql','-X','-U',ADMIN,'-d',TARGET,'--single-transaction','-v','ON_ERROR_STOP=1'],input=payload)
    upgrade_demo_schema(expected)
    snapshot=source_identity_snapshot()
    sql(TARGET,'''CREATE TABLE IF NOT EXISTS demo_known_real_identities (
        kind text NOT NULL CHECK(kind IN ('user','session')), identity_id uuid NOT NULL,
        PRIMARY KEY(kind,identity_id));''')
    # IDs are obtained dynamically, not hardcoded, printed, or credentials.
    from uuid import UUID
    rows=["('"+kind+"','"+str(UUID(value))+"')" for kind,key in [('user','users'),('session','sessions')] for value in snapshot[key]]
    if rows: sql(TARGET,'INSERT INTO demo_known_real_identities(kind,identity_id) VALUES '+','.join(rows)+' ON CONFLICT DO NOTHING;')
    configure_runtime(password)
    sql(TARGET,'REVOKE INSERT,UPDATE ON demo_known_real_identities FROM h4u_demo_app;')
    print('Isolated demo ready; history preserved unless explicit rebuild; restricted runtime configured')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--database',default=TARGET)
    parser.add_argument('--rebuild',action='store_true')
    parser.add_argument('--confirm')
    args=parser.parse_args()
    try: setup(args.database,args.rebuild,args.confirm)
    except (RuntimeError,subprocess.TimeoutExpired): raise SystemExit('Demo setup stopped safely; inspect configuration, no production database changes performed') from None
