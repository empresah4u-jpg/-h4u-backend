"""Controlled connection rejection probes using ONLY restricted demo credentials."""
import os
import psycopg


def denied_connection(host,port,database):
    try:
        conn=psycopg.connect(host=host,port=port,dbname=database,user='h4u_demo_app',
            password=os.environ['DB_PASSWORD'],connect_timeout=3,
            options='-c default_transaction_read_only=on')
    except psycopg.OperationalError as exc:
        # A network outage is inconclusive, not evidence of access control.
        reason=str(exc)
        return any(value in reason for value in ('password authentication failed','permission denied for database',
            'no pg_hba.conf entry','pg_hba.conf rejects connection')) or ('role "h4u_demo_app" does not exist' in reason)
    else:
        conn.close()
        return False
