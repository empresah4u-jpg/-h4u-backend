"""Demo isolation guards plus persistent flows in h4u_demo only; no real transfers."""
import os
import secrets
from uuid import uuid4
from unittest.mock import patch
import pytest
from app.demo.safety import configure,verify
from app.demo.provider import FakeProvider
from app.messaging.provider import MetaProvider,Settings,SendError
from app import db


@pytest.mark.parametrize('enabled,name',[('true','h4u'),('false','h4u_demo')])
def test_demo_database_mismatch_rejected_before_connect(monkeypatch,enabled,name):
    monkeypatch.setenv('H4U_DEMO_MODE',enabled); monkeypatch.setenv('DB_NAME',name)
    def forbidden(**kwargs): raise AssertionError('Should not open a connection')
    monkeypatch.setattr(db.psycopg,'connect',forbidden)
    with pytest.raises(RuntimeError,match='must match'): db.get_connection()


def test_demo_marker_is_required(monkeypatch):
    monkeypatch.setenv('H4U_DEMO_MODE','true'); monkeypatch.setenv('DB_NAME','h4u_demo')
    monkeypatch.setenv('DB_USER','h4u_demo_app')
    class Conn:
        def execute(self,query):
            self.row=('h4u_demo','h4u_demo_app',False,False,False,False,False) if 'current_database' in query else (None,)
            return self
        def fetchone(self): return self.row
    with pytest.raises(RuntimeError,match='marker missing'): verify(Conn())


@pytest.mark.parametrize('mode,database',[('true','h4u'),('false','h4u_demo')])
def test_meta_forbidden_in_demo_even_if_credentials_provided(monkeypatch,mode,database):
    monkeypatch.setenv('H4U_DEMO_MODE',mode); monkeypatch.setenv('DB_NAME',database)
    with pytest.raises(ValueError,match='disabled'):
        MetaProvider(Settings('100',access_token=secrets.token_urlsafe(32),api_version='v99.0'))


def test_meta_already_constructed_cannot_send_after_demo_enabled(monkeypatch):
    monkeypatch.setenv('H4U_DEMO_MODE','false'); monkeypatch.setenv('DB_NAME','h4u')
    adapter=MetaProvider(Settings('100',access_token=secrets.token_urlsafe(32),api_version='v99.0'))
    monkeypatch.setenv('H4U_DEMO_MODE','true')
    with pytest.raises(SendError,match='demo_external_send_forbidden'):
        adapter.send_text('00000000000','demo',str(uuid4()))


def test_configure_is_process_local_and_clears_real_provider_settings():
    with patch.dict(os.environ,{'WHATSAPP_ACCESS_TOKEN':'ephemeral-test-value','PGPASSWORD':'ephemeral-admin-value','POSTGRES_PASSWORD':'ephemeral-admin-value'},clear=False):
        configure()
        assert os.environ['DB_NAME']=='h4u_demo'
        assert os.environ['WHATSAPP_SEND_ENABLED']=='false'
        assert 'WHATSAPP_ACCESS_TOKEN' not in os.environ
        assert 'PGPASSWORD' not in os.environ and 'POSTGRES_PASSWORD' not in os.environ
        assert len(os.environ['JWT_SECRET'])>=32


@pytest.mark.parametrize('scenario,amount',[('accept','10.0'),('counter_offer','12.0')])
def test_persistent_demo_authenticated_flow(scenario,amount):
    # Requires explicit scripts.setup_demo; no schema creation/reset occurs in tests.
    with patch.dict(os.environ,clear=False):
        configure()
        from app.demo.runner import run
        summary=run(scenario)
        assert summary['status']=='completed' and summary['simulated'] is True
        steps={s['step']:s for s in summary['steps']}
        assert steps['simulated_commission']['amount']==amount
        if scenario=='counter_offer': assert 'tourist_accepts_counter_offer' in steps
        assert steps['simulated_settlement']['status']=='paid'
        assert steps['whatsapp_fake']['messages']>=5
        assert steps['whatsapp_fake']['external_calls']==0
        with db.get_connection() as conn:
            row=conn.execute('SELECT status,summary FROM demo_runs WHERE id=%s',(summary['run_id'],)).fetchone()
            assert row==('completed',summary)
            assert conn.execute("SELECT count(*) FROM message_outbox WHERE status='delivered' AND provider_message_id LIKE 'demo.%'").fetchone()[0]>0
        assert 'password' not in str(summary) and 'access_token' not in str(summary)


def test_fake_delivery_idempotency_persists():
    with patch.dict(os.environ,clear=False):
        configure(); provider=FakeProvider(); key=str(uuid4())
        assert provider.send_text('00000000000','demo',key)==provider.send_text('00000000000','demo',key)
        with db.get_connection() as conn:
            assert conn.execute('SELECT count(*) FROM demo_message_receipts WHERE message_key=%s',(key,)).fetchone()[0]==1


def test_setup_refuses_unmarked_existing_data(monkeypatch):
    from scripts import setup_demo as setup
    import json
    def sql(database,query,legacy=False):
        if 'json_object_agg' in query: return json.dumps(setup.expected_migrations())
        if 'pg_database' in query: return 't'
        if 'to_regclass' in query: return 'f'
        raise AssertionError('Unexpected query')
    monkeypatch.setattr(setup,'sql',sql)
    monkeypatch.setattr(setup,'local_config',lambda: 'test-only-unused')
    monkeypatch.setattr(setup,'command',lambda *a,**k:'isolated')
    monkeypatch.setattr(setup,'docker',lambda *a,**k:pytest.fail('No database writes allowed'))
    with pytest.raises(RuntimeError,match='not marked'): setup.setup()


@pytest.mark.parametrize('target',['h4u','postgres','h4u_demo_other'])
def test_rebuild_rejects_other_databases_before_any_action(monkeypatch,target):
    from scripts import setup_demo as setup
    monkeypatch.setattr(setup,'local_config',lambda:pytest.fail('No action before target validation'))
    with pytest.raises(RuntimeError,match='exact h4u_demo'):
        setup.setup(target,rebuild=True,confirmation=target)


def test_rebuild_requires_exact_confirmation():
    from scripts.setup_demo import validate_target
    with pytest.raises(RuntimeError,match='confirm'): validate_target('h4u_demo',True,None)
    validate_target('h4u_demo',True,'h4u_demo')


def test_runtime_superuser_rejected(monkeypatch):
    monkeypatch.setenv('H4U_DEMO_MODE','true')
    monkeypatch.setenv('DB_NAME','h4u_demo')
    monkeypatch.setenv('DB_USER','h4u_demo_app')
    class Conn:
        def execute(self,query): return self
        def fetchone(self): return ('h4u_demo','h4u_demo_app',True,False,False,False,False)
    with pytest.raises(RuntimeError,match='elevated role'): verify(Conn())


def test_runtime_privileges_and_forbidden_connections():
    from app.demo.isolation import denied_connection
    with patch.dict(os.environ,clear=False):
        configure()
        with db.get_connection() as conn:
            assert conn.execute('SELECT current_database(),current_user').fetchone()==('h4u_demo','h4u_demo_app')
            assert conn.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication FROM pg_roles WHERE rolname=current_user').fetchone()==(False,False,False,False)
            assert conn.execute("SELECT has_schema_privilege(current_user,'public','CREATE')").fetchone()==(False,)
        assert denied_connection('127.0.0.1',5432,'h4u')
        assert denied_connection(os.environ['DB_HOST'],os.environ['DB_PORT'],'postgres')
