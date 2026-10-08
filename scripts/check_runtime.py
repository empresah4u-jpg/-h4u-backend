"""Rollback-only H4U runtime rehearsal. Never persists synthetic business rows."""
import traceback
import argparse
import json
import logging
import os
import secrets
from pathlib import Path
from unittest.mock import patch
import psycopg
import pytest
from dotenv import dotenv_values


def check():
    from app.db import get_admin_connection
    from scripts.setup_runtime import PRIVATE, grants
    from scripts.apply_offer_consent import apply, NAME
    from tests import test_commercial as t
    values=dotenv_values(PRIVATE if PRIVATE.exists() else PRIVATE.with_name('.env.runtime'),interpolate=False)
    # Prove independent LOGIN, without exposing the connection string.
    with psycopg.connect(host=os.getenv('DB_HOST'),port=os.getenv('DB_PORT'),dbname='h4u',
          user=values['DB_RUNTIME_USER'],password=values['DB_RUNTIME_PASSWORD']) as runtime:
        assert runtime.execute('SELECT current_user').fetchone()==('h4u_runtime',)
        flags=runtime.execute("SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls,rolreplication FROM pg_roles WHERE rolname=current_user").fetchone()
        assert not any(flags)
        denied=False
        try:
            with runtime.transaction(force_rollback=True):runtime.execute('SET ROLE h4u')
        except psycopg.errors.InsufficientPrivilege:denied=True
        assert denied
    tests=[('settlement',lambda f:t.test_cash_to_settlement(f,False)),
           ('refund',t.test_refund_partial_duplicate_excess_and_total),
           ('reservation_cancel',t.test_reservation_duplicate_and_cancel)]
    for label,fn in tests:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(t,'get_connection',get_admin_connection)
            gen=t.flow.__wrapped__(mp);f=next(gen);conn=f['conn']
            try:
                # Existing 013 or rehearsal of exactly 013, inside outer rollback.
                if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=%s',(NAME,)).fetchone():apply(conn)
                grants(conn)
                conn.execute('SET LOCAL ROLE h4u_runtime')
                fn(f)
                if label == 'settlement':
                    from app.services import authentication as authentication
                    from app.services.passwords import hash_password
                    from app.identity import JWTSettings
                    mp.setattr(authentication,'get_connection',t.r.get_connection)
                    password=secrets.token_urlsafe(40)
                    uid=conn.execute('SELECT u.id FROM users u JOIN sessions s ON s.traveler_id=u.traveler_id WHERE s.id=%s',(f['session'],)).fetchone()[0]
                    email=conn.execute('UPDATE users SET password_hash=%s WHERE id=%s RETURNING email',(hash_password(password),uid)).fetchone()[0]
                    service=authentication.AuthenticationService(JWTSettings(secret=secrets.token_urlsafe(48)))
                    login=service.login(email,password,'127.0.0.1')
                    service.logout(login['access_token'])
                    assert conn.execute('SELECT count(*) FROM auth_sessions WHERE user_id=%s AND revoked_at IS NOT NULL',(uid,)).fetchone()==(1,)
                for statement in ('CREATE TABLE public.runtime_forbidden(id int)',
                    'ALTER TABLE reservations ADD COLUMN forbidden_test integer',
                    'DROP TABLE request_offer_consents CASCADE',
                    'TRUNCATE request_offer_consents CASCADE',
                    'UPDATE request_offer_consents SET amount_total=1 WHERE false',
                    'DELETE FROM request_offer_consents WHERE false',
                    'UPDATE schema_migrations SET checksum=checksum WHERE false',
                    'UPDATE entity_source_evidence SET fingerprint=fingerprint WHERE false',
                    'DELETE FROM entity_source_evidence WHERE false',
                    'CREATE ROLE runtime_forbidden_test'):
                    denied=False
                    try:
                        with conn.transaction(force_rollback=True):conn.execute(statement)
                    except psycopg.errors.InsufficientPrivilege:denied=True
                    assert denied, 'Negative privilege check failed'
            finally:
                try:next(gen)
                except StopIteration:pass
        print(label+': rollback PASS')
    # Actual FastAPI HTTP read endpoints with candidate runtime configuration.
    with patch.dict(os.environ,values):
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as client:
            for path in ('/health','/destinations','/hotels','/tours','/experiences/PARACAS/ballestas/commercial-options'):
                response=client.get(path)
                assert response.status_code==200,(path,response.status_code)
                if path.endswith('commercial-options'):assert not any(x['requestable'] for x in response.json()['options'])
    print('Runtime LOGIN, FastAPI reads, commercial flow and negative privileges PASS')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--confirm-database');args=parser.parse_args()
    if args.confirm_database!='h4u':raise SystemExit('Requires --confirm-database h4u')
    logging.disable(logging.CRITICAL)
    try:check()
    except Exception as exc:
        # No traceback locals, connection strings, JWTs or credentials.
        diag=getattr(exc,'diag',None)
        print(json.dumps({'location':[(Path(x.filename).name,x.lineno) for x in traceback.extract_tb(exc.__traceback__)],'error':type(exc).__name__,'sqlstate':getattr(exc,'sqlstate',None),'table':getattr(diag,'table_name',None),'permission_error':getattr(diag,'message_primary',None) if getattr(exc,'sqlstate',None)=='42501' else None}))
        raise SystemExit(1) from None

if __name__=='__main__':main()
