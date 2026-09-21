"""Exceptional first-admin provisioning, never a general account-management API."""
from app.db import get_connection
from app.services.email_validation import normalize_email
from app.services.passwords import hash_password


class BootstrapRefused(Exception):
    """A safe, non-sensitive explanation for an operator."""


def create_first_admin(email: str, password: str):
    email = normalize_email(email)
    # Reuse the exact current policy and hash outside the table lock.
    password_hash = hash_password(password)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL lock_timeout = '5s'")
        cur.execute("SET LOCAL statement_timeout = '15s'")
        # Serializes bootstraps AND other INSERT/UPDATE/DELETE writers. READ COMMITTED
        # gives the following checks a fresh snapshot after waiting for this lock.
        if cur.execute('SHOW transaction_isolation').fetchone()[0] != 'read committed':
            raise BootstrapRefused('El bootstrap requiere aislamiento READ COMMITTED.')
        cur.execute('LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE')
        if cur.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone():
            raise BootstrapRefused('Ya existe un administrador; bootstrap cancelado.')
        if cur.execute('SELECT 1 FROM users WHERE lower(email)=%s', (email,)).fetchone():
            raise BootstrapRefused('El email ya está registrado; no se modificó ninguna cuenta.')
        user_id = cur.execute(
            """INSERT INTO users(email,password_hash,role,traveler_id,partner_id,status)
               VALUES (%s,%s,'admin',NULL,NULL,'active') RETURNING id""",
            (email, password_hash),
        ).fetchone()[0]
    return user_id
