"""Real JWT/Argon2/PostgreSQL; ephemeral credentials and rollback-only user fixtures."""
import asyncio
from datetime import datetime, timezone
import secrets
import threading
from uuid import uuid4

from argon2 import PasswordHasher
from fastapi.testclient import TestClient
import jwt
import psycopg
from pydantic import SecretStr
import pytest

from app import auth, identity
from app.main import app
from app.routers import reservations, partners as partner_router, admin as admin_router
from app.services import administration as admin_service
from app.services import partner_memberships as membership_service
from app.services import authentication as service_module
from app.services.passwords import hash_password, verify_password, HASHER
from tests.test_commercial import flow, reserve, request as service_request


@pytest.fixture
def identity_case(flow, monkeypatch):
    db = flow['conn']
    # Shadow identity tables on this rollback-only connection. Never copy real
    # users, hashes, sessions or throttle buckets; all API connections use its proxy.
    for table in ('users', 'auth_sessions', 'auth_login_limits', 'partner_memberships', 'partner_events', 'admin_events'):
        db.execute(f'CREATE TEMP TABLE {table} (LIKE public.{table} INCLUDING ALL) ON COMMIT DROP')
    db.execute('ALTER TABLE pg_temp.auth_sessions ADD FOREIGN KEY (user_id) REFERENCES pg_temp.users(id)')
    for table in ('users', 'partner_memberships', 'partner_events', 'admin_events'):
        triggers = db.execute("SELECT pg_get_triggerdef(oid) FROM pg_trigger WHERE tgrelid=%s::regclass AND NOT tgisinternal", ('public.'+table,)).fetchall()
        for (definition,) in triggers:
            db.execute(definition.replace(' ON public.'+table+' ', ' ON pg_temp.'+table+' '))
    assert db.execute("SELECT 'users'::regclass::oid <> 'public.users'::regclass::oid").fetchone()[0]
    for module in (identity, service_module, auth, partner_router, membership_service, admin_router, admin_service):
        monkeypatch.setattr(module, 'get_connection', reservations.get_connection)
    # Never inspect or reuse the production JWT secret in tests.
    monkeypatch.setenv('JWT_SECRET', secrets.token_urlsafe(48))
    monkeypatch.setenv('JWT_ALGORITHM', 'HS256')
    monkeypatch.setenv('JWT_EXPIRE_MINUTES', '60')

    def make_user(role='partner', status='active', owner=None, password=None, stored_hash=None, membership=True, membership_role='owner', membership_status='active'):
        value = password or secrets.token_urlsafe(24)
        email = uuid4().hex + '@example.invalid'
        traveler_id = partner_id = None
        if role == 'partner':
            partner_id = owner or flow['partner']
        if role == 'tourist':
            traveler_id = owner or db.execute('SELECT traveler_id FROM sessions WHERE id=%s', (flow['session'],)).fetchone()[0]
        hashed = stored_hash or hash_password(value)
        uid = db.execute('''INSERT INTO users(email,password_hash,role,status,traveler_id,partner_id)
            VALUES (%s,%s,%s,%s,%s,%s) RETURNING id''',
            (email, hashed, role, status, traveler_id, partner_id)).fetchone()[0]
        if role == 'partner' and membership:
            db.execute('INSERT INTO partner_memberships(partner_id,user_id,membership_role,status) VALUES (%s,%s,%s,%s)',
                       (partner_id,uid,membership_role,membership_status))
        return {'id': uid, 'email': email, 'password': SecretStr(value), 'role': role}

    with TestClient(app, raise_server_exceptions=False) as client:
        def login(user, **changes):
            body = {'email': user['email'], 'password': user['password'].get_secret_value()}
            body.update(changes)
            return client.post('/auth/login', json=body)
        def token(user):
            response = login(user)
            assert response.status_code == 200
            return response.json()['access_token']
        yield {'db': db, 'client': client, 'flow': flow, 'user': make_user,
               'login': login, 'token': token, 'settings': app.state.auth_service.settings}


def headers(token):
    return {'Authorization': 'Bearer ' + token}


def claims(token, case):
    s = case['settings']
    return jwt.decode(token, s.secret, algorithms=['HS256'], issuer=s.issuer, audience=s.audience)


def signed(payload, case, **kwargs):
    return jwt.encode(payload, case['settings'].secret, algorithm='HS256', **kwargs)


@pytest.mark.parametrize('role', ['tourist', 'partner', 'operator', 'admin'])
def test_login_me_and_persisted_session(identity_case, role):
    t = identity_case
    user = t['user'](role=role)
    response = t['login'](user, email='  ' + user['email'].upper() + '  ')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['expires_in'] == 3600
    token = response.json()['access_token']
    result = t['client'].get('/auth/me', headers=headers(token))
    assert result.status_code == 200
    assert result.json()['role'] == role and result.json()['subject'] == str(user['id'])
    assert 'password' not in result.text and user['email'] not in result.text
    data = claims(token, t)
    assert not {'role', 'partner_id', 'traveler_id', 'password_hash', 'email'} & data.keys()
    row = t['db'].execute('SELECT user_id,revoked_at,last_seen_at FROM auth_sessions WHERE token_id=%s', (data['jti'],)).fetchone()
    assert row[0] == user['id'] and row[1] is None and row[2] is not None
    assert t['db'].execute('SELECT last_login_at IS NOT NULL FROM users WHERE id=%s', (user['id'],)).fetchone()[0]


@pytest.mark.parametrize('state', ['wrong', 'unknown', 'disabled', 'locked', 'corrupt_hash'])
def test_invalid_credentials_are_indistinguishable(identity_case, state):
    t = identity_case
    user = t['user'](status=state if state in {'disabled', 'locked'} else 'active',
                     stored_hash='not-an-argon2-hash' if state == 'corrupt_hash' else None)
    changes = {}
    if state == 'wrong':
        changes['password'] = secrets.token_urlsafe(24)
    if state == 'unknown':
        changes['email'] = uuid4().hex + '@example.invalid'
    response = t['login'](user, **changes)
    assert response.status_code == 401
    assert response.json() == {'detail': 'Credenciales no válidas.'}
    assert response.headers['www-authenticate'] == 'Bearer'
    assert response.headers['cache-control'] == 'no-store'
    assert t['db'].execute('SELECT count(*) FROM auth_sessions WHERE user_id=%s', (user['id'],)).fetchone()[0] == 0
    assert t['db'].execute('SELECT last_login_at FROM users WHERE id=%s', (user['id'],)).fetchone()[0] is None


def test_logout_revokes_only_current_session(identity_case):
    t = identity_case
    user = t['user']()
    first, second = t['token'](user), t['token'](user)
    assert t['client'].post('/auth/logout', headers=headers(first)).status_code == 204
    assert t['client'].get('/auth/me', headers=headers(first)).status_code == 401
    assert t['client'].post('/auth/logout', headers=headers(first)).status_code == 401
    assert t['client'].get('/auth/me', headers=headers(second)).status_code == 200
    assert t['db'].execute('SELECT count(*) FROM auth_sessions WHERE user_id=%s', (user['id'],)).fetchone()[0] == 2


def test_logout_all_invalidates_tokens_and_preserves_other_users(identity_case):
    t = identity_case
    user, other = t['user'](), t['user'](role='tourist')
    first, second, untouched = t['token'](user), t['token'](user), t['token'](other)
    assert t['client'].post('/auth/logout-all', headers=headers(first)).status_code == 204
    for token in (first, second):
        assert t['client'].get('/auth/me', headers=headers(token)).status_code == 401
    assert t['client'].get('/auth/me', headers=headers(untouched)).status_code == 200
    assert t['db'].execute('SELECT token_version FROM users WHERE id=%s', (user['id'],)).fetchone()[0] == 1
    fresh = t['token'](user)
    assert claims(fresh, t)['ver'] == 1
    assert t['client'].get('/auth/me', headers=headers(fresh)).status_code == 200


@pytest.mark.parametrize('change', ['disabled', 'locked', 'role', 'password', 'owner', 'version'])
def test_identity_change_revokes_existing_tokens(identity_case, change):
    t = identity_case
    user = t['user']()
    token = t['token'](user)
    db = t['db']
    if change in {'disabled', 'locked'}:
        db.execute('UPDATE users SET status=%s WHERE id=%s', (change, user['id']))
        # Reactivation must not resurrect tokens issued before suspension.
        db.execute("UPDATE users SET status='active' WHERE id=%s", (user['id'],))
    elif change == 'role':
        db.execute("UPDATE users SET role='admin',partner_id=NULL WHERE id=%s", (user['id'],))
    elif change == 'password':
        db.execute('UPDATE users SET password_hash=%s WHERE id=%s', (hash_password(secrets.token_urlsafe(24)), user['id']))
    elif change == 'owner':
        owner = db.execute("INSERT INTO partners(code,business_name) VALUES (%s,'Auth test') RETURNING id", (uuid4().hex[:20],)).fetchone()[0]
        db.execute('UPDATE users SET partner_id=%s WHERE id=%s', (owner, user['id']))
    else:
        db.execute('UPDATE users SET token_version=token_version+1 WHERE id=%s', (user['id'],))
    assert t['client'].get('/auth/me', headers=headers(token)).status_code == 401
    assert db.execute('SELECT count(*) FROM auth_sessions WHERE user_id=%s AND revoked_at IS NULL', (user['id'],)).fetchone()[0] == 0


def test_token_version_cannot_decrease(identity_case):
    t = identity_case
    user = t['user']()
    t['db'].execute('UPDATE users SET token_version=2 WHERE id=%s', (user['id'],))
    with pytest.raises(psycopg.errors.CheckViolation):
        with t['db'].transaction():
            t['db'].execute('UPDATE users SET token_version=1 WHERE id=%s', (user['id'],))


@pytest.mark.parametrize('claim', ['sub', 'jti', 'exp', 'iat', 'ver', 'iss', 'aud', 'token_use'])
def test_required_jwt_claims(identity_case, claim):
    t = identity_case
    payload = claims(t['token'](t['user']()), t)
    payload.pop(claim)
    assert t['client'].get('/auth/me', headers=headers(signed(payload, t))).status_code == 401


@pytest.mark.parametrize('name,value', [
    ('ver', True), ('ver', '0'), ('ver', -1), ('ver', 0.5),
    ('exp', '9999999999'), ('exp', float('inf')), ('iat', False),
    ('sub', 'not-uuid'), ('sub', 42), ('jti', 'not-uuid'),
    ('iss', 'another-issuer'), ('aud', 'another-audience'), ('aud', ['h4u-api']),
    ('token_use', 'refresh'),
])
def test_invalid_jwt_types_and_scope(identity_case, name, value):
    t = identity_case
    payload = claims(t['token'](t['user']()), t)
    payload[name] = value
    assert t['client'].get('/auth/me', headers=headers(signed(payload, t))).status_code == 401


@pytest.mark.parametrize('mode', ['expired', 'future_iat', 'future_nbf', 'too_long', 'wrong_signature', 'unsigned', 'wrong_algorithm', 'wrong_typ'])
def test_jwt_signature_and_time_validation(identity_case, mode):
    t = identity_case
    token = t['token'](t['user']())
    payload = claims(token, t)
    if mode == 'expired':
        payload['iat'] -= 7200
        payload['exp'] -= 7200
    elif mode == 'future_iat':
        payload['iat'] += 3600
        payload['exp'] += 3600
    elif mode == 'future_nbf':
        payload['nbf'] = payload['iat'] + 3600
    elif mode == 'too_long':
        payload['exp'] += 1
    token = signed(payload, t)
    if mode == 'wrong_signature':
        token = jwt.encode(payload, secrets.token_urlsafe(48), algorithm='HS256')
    elif mode == 'unsigned':
        token = jwt.encode(payload, None, algorithm='none')
    elif mode == 'wrong_algorithm':
        token = jwt.encode(payload, t['settings'].secret, algorithm='HS384')
    elif mode == 'wrong_typ':
        token = signed(payload, t, headers={'typ': 'refresh'})
    assert t['client'].get('/auth/me', headers=headers(token)).status_code == 401


@pytest.mark.parametrize('mode', ['expired', 'revoked', 'missing', 'wrong_user', 'wrong_version'])
def test_server_session_is_required(identity_case, mode):
    t = identity_case
    user = t['user']()
    token = t['token'](user)
    payload = claims(token, t)
    if mode == 'expired':
        t['db'].execute("UPDATE auth_sessions SET created_at=clock_timestamp()-INTERVAL '2 hours',expires_at=clock_timestamp()-INTERVAL '1 hour' WHERE token_id=%s", (payload['jti'],))
    elif mode == 'revoked':
        t['db'].execute('UPDATE auth_sessions SET revoked_at=clock_timestamp() WHERE token_id=%s', (payload['jti'],))
    elif mode == 'missing':
        payload['jti'] = str(uuid4())
    elif mode == 'wrong_user':
        payload['sub'] = str(t['user'](role='tourist')['id'])
    else:
        payload['ver'] += 1
    assert t['client'].get('/auth/me', headers=headers(signed(payload, t))).status_code == 401


def test_client_cannot_choose_privileges(identity_case):
    t = identity_case
    user = t['user']()
    assert t['login'](user, role='admin').status_code == 422
    token = t['token'](user)
    payload = claims(token, t)
    payload.update(role='admin', partner_id=str(uuid4()), traveler_id=str(uuid4()))
    forged = signed(payload, t)
    result = t['client'].get('/auth/me', headers={**headers(forged), 'X-Role': 'admin'})
    assert result.status_code == 200
    assert result.json()['role'] == 'partner'
    assert result.json()['partner_id'] == str(t['flow']['partner'])
    assert t['client'].post('/settlements/process-overdue', headers=headers(forged)).status_code == 403


def test_real_token_partner_ownership(identity_case):
    t = identity_case
    res = reserve(t['flow'])
    other = t['db'].execute("INSERT INTO partners(code,business_name) VALUES (%s,'Auth test') RETURNING id", (uuid4().hex[:20],)).fetchone()[0]
    foreign = t['token'](t['user'](owner=other))
    path = '/reservations/' + res['code'] + '/cancel'
    assert t['client'].post(path, json={'reason': 'test'}, headers=headers(foreign)).status_code == 403
    own = t['token'](t['user']())
    assert t['client'].post(path, json={'reason': 'test'}, headers=headers(own)).status_code == 200


def test_real_token_tourist_isolation(identity_case):
    t = identity_case
    req = service_request(t['flow'])
    token = t['token'](t['user'](role='tourist'))
    body = {'request_partner_id': req['candidates'][0]['request_partner_id'], 'action': 'accept'}
    assert t['client'].post('/partner-responses', json=body, headers=headers(token)).status_code == 403


def test_invalid_auth_input_does_not_echo_password(identity_case):
    t = identity_case
    sensitive = secrets.token_urlsafe(32) * 40
    response = t['client'].post('/auth/login', json={'email': uuid4().hex+'@example.invalid', 'password': sensitive})
    assert response.status_code == 422 and sensitive not in response.text
    assert 'input' not in response.text
    assert response.headers['cache-control'] == 'no-store'


def test_throttling_persists_failed_attempts_without_plaintext(identity_case):
    t = identity_case
    user = t['user']()
    for _ in range(10):
        assert t['login'](user, password=secrets.token_urlsafe(24)).status_code == 401
    response = t['login'](user)
    assert response.status_code == 429 and response.headers['retry-after'] == '600'
    rows = t['db'].execute('SELECT key_hash,attempts FROM auth_login_limits').fetchall()
    assert len(rows) == 2 and all(len(row[0]) == 64 for row in rows)
    assert all(user['email'] not in row[0] for row in rows)
    t['db'].execute("UPDATE auth_login_limits SET window_started_at=clock_timestamp()-INTERVAL '11 minutes'")
    assert t['login'](user).status_code == 200


def test_argon2_rehash_issues_current_version(identity_case):
    t = identity_case
    password = secrets.token_urlsafe(24)
    legacy = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash(password)
    user = t['user'](password=password, stored_hash=legacy)
    token = t['token'](user)
    row = t['db'].execute('SELECT password_hash,token_version FROM users WHERE id=%s', (user['id'],)).fetchone()
    assert row[0] != legacy and not HASHER.check_needs_rehash(row[0])
    assert verify_password(password, row[0]) and row[1] == claims(token, t)['ver']
    assert t['client'].get('/auth/me', headers=headers(token)).status_code == 200


def test_failed_signing_creates_no_session(identity_case, monkeypatch):
    t = identity_case
    user = t['user']()
    def fail(*args, **kwargs):
        raise RuntimeError('simulated signing failure')
    monkeypatch.setattr(service_module, 'issue_access_token', fail)
    assert t['login'](user).status_code == 500
    assert t['db'].execute('SELECT count(*) FROM auth_sessions WHERE user_id=%s', (user['id'],)).fetchone()[0] == 0
    assert t['db'].execute('SELECT last_login_at FROM users WHERE id=%s', (user['id'],)).fetchone()[0] is None


def test_identity_does_not_block_event_loop(monkeypatch):
    provider = identity.JWTIdentityProvider(identity.JWTSettings(secret=secrets.token_urlsafe(48)))
    loop_thread = threading.get_ident()
    calls = []
    def authenticate_sync(token):
        calls.append(threading.get_ident())
        return None
    monkeypatch.setattr(provider, 'authenticate_sync', authenticate_sync)
    assert asyncio.run(provider.authenticate('test-placeholder')) is None
    assert calls and calls[0] != loop_thread


def test_missing_config_fails_closed_without_exposing_it(monkeypatch):
    monkeypatch.delenv('JWT_SECRET', raising=False)
    with TestClient(app) as client:
        assert app.state.identity_provider is None
        assert client.post('/auth/login', json={}).status_code == 503
        assert client.get('/auth/me').status_code == 401
        assert client.get('/openapi.json').status_code == 200


@pytest.mark.parametrize('options', [{'expire_minutes': 0}, {'expire_minutes': 61}, {'algorithm': 'none'}, {'algorithm': 'HS384'}])
def test_unsafe_settings_rejected(options):
    with pytest.raises(identity.AuthConfigurationError):
        identity.JWTSettings(secret=secrets.token_urlsafe(48), **options)


def test_password_hash_is_salted_argon2id():
    password = secrets.token_urlsafe(24)
    first, second = hash_password(password), hash_password(password)
    assert first != second and first.startswith('$argon2id$')
    assert verify_password(password, first) and not verify_password(secrets.token_urlsafe(24), first)
