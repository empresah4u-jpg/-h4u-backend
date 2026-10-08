"""Explicit H4U runtime grants; no catalog writes, ownership or credential rotation."""
import argparse
import os
from pathlib import Path
import secrets
from dotenv import dotenv_values
from psycopg import sql

ROLE = 'h4u_runtime'
PRIVATE = Path(__file__).resolve().parents[1] / '.env.runtime.pending'
READ = '''destinations hotels restaurants tours attractions emergency_services general_services
transport_providers transport_routes tour_operators venues entity_embeddings experience_products
products product_partners partners travelers sessions users auth_sessions auth_login_limits
partner_memberships partner_events admin_events service_requests request_partners request_passengers
reservations payments refunds commissions commission_adjustments partner_settlements
settlement_adjustments settlement_commissions financial_events cancellation_policy_versions
cancellation_policy_assignments cancellation_cases commercial_product_settings commercial_slots
refund_commands notification_events channel_threads channel_binding_events inbound_messages message_outbox
request_offer_consents data_sources entity_sources entity_source_evidence schema_migrations'''.split()
INSERT = '''users auth_sessions auth_login_limits partner_memberships partner_events admin_events partners
service_requests request_partners request_passengers reservations payments refunds commissions
commission_adjustments partner_settlements settlement_adjustments settlement_commissions financial_events
cancellation_policy_versions cancellation_policy_assignments cancellation_cases commercial_product_settings
commercial_slots refund_commands notification_events channel_threads channel_binding_events inbound_messages
message_outbox request_offer_consents'''.split()
UPDATE = '''users auth_sessions auth_login_limits partner_memberships partners service_requests request_partners
reservations payments refunds partner_settlements cancellation_policy_assignments commercial_product_settings
commercial_slots refund_commands notification_events channel_threads inbound_messages message_outbox'''.split()


def grant_table_access(conn, role=ROLE):
    """Same table/column policy for production setup and isolated E2E verification."""
    who = sql.Identifier(role)
    conn.execute(sql.SQL('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}').format(who))
    conn.execute(sql.SQL('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {}').format(who))
    tables={r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    for permission,names in [('SELECT',READ),('INSERT',INSERT),('UPDATE',UPDATE)]:
        for name in sorted(set(names)&tables):
            conn.execute(sql.SQL('GRANT {} ON TABLE {} TO {}').format(sql.SQL(permission),sql.Identifier(name),who))
    # Row locks require UPDATE on at least one column; no catalog write endpoint.
    for table in ('products','product_partners','commission_adjustments','cancellation_policy_versions','sessions','travelers','request_passengers'):
        conn.execute(sql.SQL('GRANT UPDATE(id) ON {} TO {}').format(sql.Identifier(table),who))
    conn.execute(sql.SQL('GRANT UPDATE(status,settled_at,updated_at) ON commissions TO {}').format(who))
    conn.execute(sql.SQL('GRANT UPDATE(first_name,last_name,birth_date,nationality_code,document_type,document_number,is_primary_passenger,updated_at) ON request_passengers TO {}').format(who))


def grants(conn, role=ROLE):
    """Call within migration transaction. Explicit allowlist, fail closed for new objects."""
    who = sql.Identifier(role)
    conn.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
    conn.execute('REVOKE TEMPORARY ON DATABASE h4u FROM PUBLIC')
    conn.execute(sql.SQL('GRANT CONNECT ON DATABASE h4u TO {}').format(who))
    conn.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(who))
    grant_table_access(conn, role)
    # Trigger functions need no direct runtime EXECUTE; exclude extension objects.
    funcs=conn.execute("""SELECT p.oid::regprocedure::text FROM pg_proc p
        WHERE p.pronamespace='public'::regnamespace AND NOT EXISTS
        (SELECT 1 FROM pg_depend d WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid AND d.deptype='e')""").fetchall()
    for (fn,) in funcs:
        conn.execute(sql.SQL('REVOKE EXECUTE ON FUNCTION {} FROM PUBLIC, {}').format(sql.SQL(fn),who))
    if conn.execute("SELECT to_regprocedure('request_consent_terms(service_requests)')").fetchone()[0]:
        conn.execute(sql.SQL('GRANT EXECUTE ON FUNCTION request_consent_terms(service_requests) TO {}').format(who))
    conn.execute('ALTER DEFAULT PRIVILEGES FOR ROLE h4u IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC')
    # UUID primary keys: no application sequence usage is required.


def setup():
    from app.db import get_admin_connection
    from scripts.setup_demo import write_private
    private = PRIVATE.with_name('.env.runtime') if PRIVATE.with_name('.env.runtime').exists() else PRIVATE
    if not private.exists():
        write_private(private, 'DB_RUNTIME_USER=h4u_runtime\nDB_RUNTIME_PASSWORD='+secrets.token_urlsafe(48)+'\n')
    if private.stat().st_mode & 0o077:
        raise RuntimeError('Runtime credential file must be private (0600)')
    values=dotenv_values(private,interpolate=False)
    password=values.get('DB_RUNTIME_PASSWORD','')
    if values.get('DB_RUNTIME_USER')!=ROLE or len(password)<32:
        raise RuntimeError('Invalid runtime credential configuration')
    with get_admin_connection() as conn:
        if conn.execute('SELECT current_database(),current_user').fetchone()!=('h4u','h4u'):
            raise RuntimeError('Expected H4U administrative connection')
        conn.execute("SET LOCAL log_statement='none'; SET LOCAL log_min_error_statement='panic'")
        row=conn.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls,rolreplication FROM pg_roles WHERE rolname=%s',(ROLE,)).fetchone()
        if row is None:
            conn.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION NOINHERIT PASSWORD {}').format(sql.Identifier(ROLE),sql.Literal(password)))
        elif any(row):
            raise RuntimeError('Unexpected existing runtime privileges')
        if conn.execute('SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=%s)',(ROLE,)).fetchone():
            raise RuntimeError('Runtime must not inherit other roles')
        grants(conn)
    print('Runtime role and explicit grants configured; existing credentials unchanged.')


def main():
    p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');p.add_argument('--confirm-database')
    a=p.parse_args()
    if not a.apply or a.confirm_database!='h4u':
        raise SystemExit('Requires --apply --confirm-database h4u')
    try: setup()
    except Exception as exc: raise SystemExit('Runtime setup failed: '+type(exc).__name__) from None

if __name__=='__main__':main()
