"""Synchronous authentication service: FastAPI runs its handlers in worker threads."""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from uuid import uuid4

from app.db import get_connection
from app.identity import JWTSettings, decode_access_token, issue_access_token
from app.services.passwords import HASHER, dummy_hash, hash_password, verify_password


class InvalidCredentials(Exception):
    pass


class LoginLimited(Exception):
    pass


class AuthenticationService:
    def __init__(self, settings: JWTSettings):
        self.settings = settings
        # Generated once in a worker at startup, never a fixed password/hash in Git.
        self._dummy_hash = dummy_hash()

    def _login_limits(self, email: str, client_address: str):
        # Never trust a body/header-supplied identity or X-Forwarded-For here.
        buckets = [('email:' + email, 10), ('address:' + client_address, 30)]
        keys = sorted((hmac.new(self.settings.secret.encode(), label.encode(), hashlib.sha256).hexdigest(), limit)
                      for label, limit in buckets)
        limited = False
        with get_connection() as conn:
            with conn.cursor() as cur:
                for key, limit in keys:
                    cur.execute('''INSERT INTO auth_login_limits(key_hash,attempts)
                        VALUES (%s,1) ON CONFLICT(key_hash) DO UPDATE SET
                        attempts=CASE WHEN auth_login_limits.window_started_at <= clock_timestamp()-INTERVAL '10 minutes'
                                      THEN 1 ELSE LEAST(auth_login_limits.attempts+1,%s) END,
                        window_started_at=CASE WHEN auth_login_limits.window_started_at <= clock_timestamp()-INTERVAL '10 minutes'
                                               THEN clock_timestamp() ELSE auth_login_limits.window_started_at END
                        RETURNING attempts''', (key, limit + 1))
                    limited = cur.fetchone()[0] > limit or limited
        # Raise only after commit: failed/limited attempts must not roll back counters.
        if limited:
            raise LoginLimited()

    def login(self, email: str, password: str, client_address: str):
        email = email.strip().lower()
        self._login_limits(email, client_address)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT id,password_hash,status,token_version FROM users
                               WHERE lower(email)=%s FOR UPDATE''', (email,))
                user = cur.fetchone()
                valid = verify_password(password, user[1] if user else self._dummy_hash)
                if not valid or user is None or user[2] != 'active':
                    raise InvalidCredentials()
                token_version = user[3]
                if HASHER.check_needs_rehash(user[1]):
                    # Same verified password, stronger encoding. Never change its value.
                    cur.execute('UPDATE users SET password_hash=%s,updated_at=clock_timestamp() WHERE id=%s RETURNING token_version',
                                (hash_password(password), user[0]))
                    token_version = cur.fetchone()[0]
                now = datetime.now(timezone.utc).replace(microsecond=0)
                expires_at = now + timedelta(minutes=self.settings.expire_minutes)
                token_id = uuid4()
                token = issue_access_token(user[0], token_id, token_version, now, expires_at, self.settings)
                cur.execute('''INSERT INTO auth_sessions(user_id,token_id,created_at,expires_at)
                               VALUES (%s,%s,%s,%s)''', (user[0], token_id, now, expires_at))
                cur.execute('UPDATE users SET last_login_at=clock_timestamp(),updated_at=clock_timestamp() WHERE id=%s', (user[0],))
        # A token is returned only once its server-side session has committed.
        return {'access_token': token, 'token_type': 'bearer',
                'expires_in': self.settings.expire_minutes * 60}

    def logout(self, token: str, all_sessions: bool = False):
        claims = decode_access_token(token, self.settings)
        if claims is None:
            raise InvalidCredentials()
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT status,token_version FROM users WHERE id=%s FOR UPDATE', (claims.user_id,))
                user = cur.fetchone()
                if user is None or user[0] != 'active' or user[1] != claims.version:
                    raise InvalidCredentials()
                cur.execute('''SELECT id FROM auth_sessions WHERE user_id=%s AND token_id=%s
                               AND revoked_at IS NULL AND expires_at>clock_timestamp() FOR UPDATE''',
                            (claims.user_id, claims.token_id))
                session = cur.fetchone()
                if session is None or claims.expires_at <= datetime.now(timezone.utc).timestamp():
                    raise InvalidCredentials()
                if all_sessions:
                    cur.execute('''UPDATE users SET token_version=token_version+1,updated_at=clock_timestamp()
                                   WHERE id=%s''', (claims.user_id,))
                    cur.execute('''UPDATE auth_sessions SET revoked_at=clock_timestamp()
                                   WHERE user_id=%s AND revoked_at IS NULL''', (claims.user_id,))
                else:
                    cur.execute('UPDATE auth_sessions SET revoked_at=clock_timestamp() WHERE id=%s', (session[0],))
