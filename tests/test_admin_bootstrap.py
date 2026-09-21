"""First-admin bootstrap with ephemeral credentials and rollback-only SQL."""
from contextlib import contextmanager
import getpass
import secrets
from uuid import uuid4

import psycopg
import pytest

from app.routers import reservations
from app.services import admin_bootstrap as bootstrap
from app.services.passwords import verify_password
from scripts import create_admin as cli
from tests.test_commercial import flow
from tests.test_identity_auth import identity_case, headers


@pytest.fixture
def case(identity_case, monkeypatch):
    monkeypatch.setattr(bootstrap, 'get_connection', reservations.get_connection)
    assert identity_case['db'].execute("SELECT count(*) FROM users WHERE role='admin'").fetchone()[0] == 0
    return identity_case


def credentials():
    return uuid4().hex + '@example.invalid', secrets.token_urlsafe(24)


def test_bootstrap_login_jwt_me_and_policies(case):
    email, password = credentials()
    uid = bootstrap.create_first_admin('  ' + email.upper() + '  ', password)
    row = case['db'].execute('SELECT email,password_hash,role,traveler_id,partner_id,status FROM users WHERE id=%s', (uid,)).fetchone()
    assert row[0] == email
    assert row[1].startswith('$argon2id$') and row[1] != password
    assert verify_password(password, row[1])
    assert row[2:] == ('admin', None, None, 'active')
    result = case['client'].post('/auth/login', json={'email': email, 'password': password})
    assert result.status_code == 200
    token = result.json()['access_token']
    me = case['client'].get('/auth/me', headers=headers(token))
    assert me.status_code == 200
    assert me.json() == {'subject': str(uid), 'role': 'admin', 'traveler_id': None, 'partner_id': None}
    assert case['db'].execute('SELECT count(*) FROM auth_sessions WHERE user_id=%s', (uid,)).fetchone()[0] == 1
    # A real admin passes authorization; resource lookup then returns 404.
    response = case['client'].post('/refunds', headers=headers(token), json={'payment_code': 'MISSING', 'amount': 1, 'reason': 'test', 'idempotency_key': uuid4().hex})
    assert response.status_code == 404
    assert case['client'].post('/auth/logout', headers=headers(token)).status_code == 204
    assert case['client'].get('/auth/me', headers=headers(token)).status_code == 401


@pytest.mark.parametrize('email', ['', 'no-at', 'a@@b', 'a b@example.invalid', 'á@example.invalid', 'x'*65+'@example.invalid'])
def test_invalid_email(case, email):
    with pytest.raises(ValueError):
        bootstrap.create_first_admin(email, secrets.token_urlsafe(24))
    assert case['db'].execute("SELECT count(*) FROM users WHERE role='admin'").fetchone()[0] == 0


@pytest.mark.parametrize('password', ['', 'x'*11, 'x'*1025, 'é'*513])
def test_invalid_password(case, password):
    with pytest.raises(ValueError):
        bootstrap.create_first_admin(credentials()[0], password)
    assert case['db'].execute("SELECT count(*) FROM users WHERE role='admin'").fetchone()[0] == 0


@pytest.mark.parametrize('status', ['active', 'disabled', 'locked'])
def test_existing_admin_blocks_repeated_bootstrap(case, status):
    user = case['user'](role='admin', status=status)
    with pytest.raises(bootstrap.BootstrapRefused, match='Ya existe'):
        bootstrap.create_first_admin(*credentials())
    assert case['db'].execute("SELECT id FROM users WHERE role='admin'").fetchall() == [(user['id'],)]


def test_duplicate_email_does_not_promote_existing_user(case):
    user = case['user'](role='operator')
    with pytest.raises(bootstrap.BootstrapRefused, match='registrado'):
        bootstrap.create_first_admin(user['email'].upper(), credentials()[1])
    assert case['db'].execute('SELECT role FROM users WHERE id=%s', (user['id'],)).fetchone()[0] == 'operator'


def test_failure_after_insert_rolls_back(case, monkeypatch):
    original = reservations.get_connection
    @contextmanager
    def fail_before_commit():
        with original() as conn:
            yield conn
            raise RuntimeError('injected failure before commit')
    monkeypatch.setattr(bootstrap, 'get_connection', fail_before_commit)
    with pytest.raises(RuntimeError):
        bootstrap.create_first_admin(*credentials())
    assert case['db'].execute("SELECT count(*) FROM users WHERE role='admin'").fetchone()[0] == 0


def test_bootstrap_lock_excludes_concurrent_writers(case):
    bootstrap.create_first_admin(*credentials())
    # PostgreSQL retains this writer-excluding lock until the outer rollback.
    # Do not try to lock public.users: it contains the real administrator.
    locks = case['db'].execute("""SELECT mode FROM pg_locks
        WHERE pid=pg_backend_pid() AND relation='pg_temp.users'::regclass
          AND granted""").fetchall()
    assert ('ShareRowExclusiveLock',) in locks


def prepare_cli(monkeypatch, email, password):
    monkeypatch.setattr(cli.sys, 'argv', ['create_admin'])
    monkeypatch.setattr(cli.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr('builtins.input', lambda _: email)
    monkeypatch.setattr(cli.getpass, 'getpass', lambda _: password)


def test_cli_success_no_credentials_in_output(case, monkeypatch, capsys):
    email, password = credentials()
    prepare_cli(monkeypatch, email, password)
    assert cli.main() == 0
    output = capsys.readouterr()
    assert password not in output.out + output.err
    assert email not in output.out + output.err
    assert '$argon2' not in output.out + output.err


def test_cli_error_does_not_print_exception_secrets(monkeypatch, capsys):
    email, password = credentials()
    prepare_cli(monkeypatch, email, password)
    def fail(*args):
        raise psycopg.DatabaseError(password)
    monkeypatch.setattr(cli, 'create_first_admin', fail)
    assert cli.main() == 1
    output = capsys.readouterr()
    assert password not in output.out + output.err


@pytest.mark.parametrize('scenario', ['arguments', 'non_tty', 'echo_fallback', 'mismatch', 'cancel'])
def test_cli_refuses_unsafe_or_cancelled_input(monkeypatch, capsys, scenario):
    email, password = credentials()
    prepare_cli(monkeypatch, email, password)
    def must_not_call(*args):
        pytest.fail('database provisioning must not be reached')
    monkeypatch.setattr(cli, 'create_first_admin', must_not_call)
    if scenario == 'arguments':
        monkeypatch.setattr(cli.sys, 'argv', ['create_admin', password])
    elif scenario == 'non_tty':
        monkeypatch.setattr(cli.sys.stdin, 'isatty', lambda: False)
    elif scenario == 'mismatch':
        values = iter([password, secrets.token_urlsafe(24)])
        monkeypatch.setattr(cli.getpass, 'getpass', lambda _: next(values))
    else:
        def interrupt(_):
            if scenario == 'cancel':
                raise KeyboardInterrupt
            import warnings
            warnings.warn('unsafe terminal', getpass.GetPassWarning)
        monkeypatch.setattr(cli.getpass, 'getpass', interrupt)
    assert cli.main() != 0
    output = capsys.readouterr()
    assert password not in output.out + output.err
