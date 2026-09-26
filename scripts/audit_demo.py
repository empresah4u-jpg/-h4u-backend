"""Read-only integrity checks for the isolated demo, without messages or credentials."""
import json
from hashlib import sha256
from pathlib import Path
from app.demo.safety import configure


def main():
    configure()
    from app.db import get_connection
    from scripts.audit_whatsapp import audit as audit_messaging
    from app.demo.isolation import denied_connection
    import os
    with get_connection() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        checks=audit_messaging(conn)
        dbname,role,superuser,createdb,createrole,replication,bypass=conn.execute(
            'SELECT current_database(),current_user,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
        checks['wrong_database']=int(dbname!='h4u_demo')
        checks['wrong_runtime_role']=int(role!='h4u_demo_app')
        checks['elevated_runtime_role']=int(any((superuser,createdb,createrole,replication,bypass)))
        checks['invalid_marker']=int(conn.execute('SELECT purpose FROM demo_environment WHERE singleton').fetchone()!=('h4u-persistent-demo',))
        checks['other_database_connect']=conn.execute("SELECT count(*) FROM pg_database WHERE datname<>'h4u_demo' AND datallowconn AND has_database_privilege(current_user,oid,'CONNECT')").fetchone()[0]
        checks['runtime_ddl_privilege']=int(conn.execute("SELECT has_schema_privilege(current_user,'public','CREATE')").fetchone()[0])
        checks['known_real_users_copied']=conn.execute("SELECT count(*) FROM users u JOIN demo_known_real_identities k ON k.identity_id=u.id AND k.kind='user'").fetchone()[0]
        checks['known_real_sessions_copied']=conn.execute("SELECT count(*) FROM auth_sessions s JOIN demo_known_real_identities k ON k.identity_id=s.id AND k.kind='session'").fetchone()[0]
        checks['nonfictional_users']=conn.execute("SELECT count(*) FROM users WHERE email !~ '^(admin|partner|tourist)\\.[a-f0-9]{12,16}@example\\.invalid$'").fetchone()[0]
        checks['nonfake_outbox']=conn.execute("SELECT count(*) FROM message_outbox WHERE provider_message_id IS NOT NULL AND provider_message_id NOT LIKE 'demo.%'").fetchone()[0]
        checks['demo_delivery_without_receipt']=conn.execute('''SELECT count(*) FROM message_outbox o
            LEFT JOIN demo_message_receipts d ON d.message_key=o.id AND d.provider_message_id=o.provider_message_id
            WHERE o.status IN ('sent','delivered','read') AND d.message_key IS NULL''').fetchone()[0]
        checks['completed_without_simulated_settlement']=conn.execute('''SELECT count(*) FROM demo_runs d
            WHERE d.status='completed' AND NOT EXISTS (
              SELECT 1 FROM jsonb_array_elements(d.summary->'steps') s
              JOIN partner_settlements p ON p.code=s->>'code'
              WHERE s->>'step'='simulated_settlement' AND p.status='paid')''').fetchone()[0]
        checks['completed_finance_mismatch']=conn.execute("""WITH runs AS (
            SELECT scenario,
             EXISTS(SELECT 1 FROM jsonb_array_elements(summary->'steps') s WHERE s->>'step'='lifecycle_refund') AS lifecycle_refund,
             (SELECT s->>'code' FROM jsonb_array_elements(summary->'steps') s WHERE s->>'step'='simulated_payment') payment_code,
             (SELECT s->>'code' FROM jsonb_array_elements(summary->'steps') s WHERE s->>'step'='simulated_settlement') settlement_code
            FROM demo_runs WHERE status='completed')
            SELECT count(*) FROM runs x LEFT JOIN payments p ON p.code=x.payment_code
            LEFT JOIN reservations r ON r.id=p.reservation_id LEFT JOIN commissions c ON c.payment_id=p.id
            LEFT JOIN partner_settlements s ON s.code=x.settlement_code
            WHERE p.id IS NULL OR c.id IS NULL OR s.id IS NULL OR p.status<>CASE WHEN x.lifecycle_refund THEN 'refunded' ELSE 'paid' END OR s.status<>'paid'
             OR p.amount<>CASE WHEN x.scenario='accept' THEN 100 ELSE 120 END
             OR c.commission_amount<>CASE WHEN x.scenario='accept' THEN 10 ELSE 12 END
             OR s.total_commission_amount<>c.commission_amount OR r.agreed_price<>p.amount
             OR c.partner_id<>s.partner_id OR r.partner_id<>s.partner_id
             OR p.currency<>'PEN' OR c.currency<>'PEN' OR s.currency<>'PEN'""").fetchone()[0]
        expected={p.name:sha256(p.read_bytes()).hexdigest() for p in Path('db/migrations').glob('*.sql') if not p.name.startswith('001_')}
        actual=dict(conn.execute('SELECT version,checksum FROM schema_migrations').fetchall())
        checks['migration_mismatch']=int(expected!=actual)
        runs=dict(conn.execute('SELECT status,count(*) FROM demo_runs GROUP BY status').fetchall())
    checks['source_connection_not_proven_denied']=int(not denied_connection('127.0.0.1',5432,'h4u'))
    checks['admin_database_connection_not_denied']=int(not denied_connection(os.environ['DB_HOST'],os.environ['DB_PORT'],'postgres'))
    print(json.dumps({'database':'h4u_demo','checks':checks,'runs':runs},indent=2))
    return int(any(checks.values()))


if __name__=='__main__': raise SystemExit(main())
