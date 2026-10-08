"""Connection routing checks without reading local credentials or using PostgreSQL."""
import pytest
from app import db


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setenv('DB_NAME','h4u')
    monkeypatch.setenv('DB_USER','h4u')
    monkeypatch.setenv('DB_PASSWORD','synthetic-admin')
    monkeypatch.setenv('H4U_DEMO_MODE','false')
    monkeypatch.delenv('DB_RUNTIME_USER',raising=False)
    monkeypatch.delenv('DB_RUNTIME_PASSWORD',raising=False)
    monkeypatch.setattr(db,'dotenv_values',lambda *a,**k:{})
    calls=[]
    class Fake:
        closed=False
        def execute(self,*a):return self
        def fetchone(self):return (False,False,False,False,False)
        def commit(self):pass
        def close(self):self.closed=True
    connection=Fake()
    monkeypatch.setattr(db.psycopg,'connect',lambda **kw: calls.append(kw) or connection)
    return calls,connection


def test_no_admin_fallback(isolated):
    with pytest.raises(RuntimeError):db.get_connection()
    assert not isolated[0]


def test_runtime_connection(isolated,monkeypatch):
    monkeypatch.setenv('DB_RUNTIME_USER','h4u_runtime')
    monkeypatch.setenv('DB_RUNTIME_PASSWORD','synthetic-runtime')
    db.get_connection()
    assert isolated[0][0]['user']=='h4u_runtime'
    assert isolated[0][0]['password']=='synthetic-runtime'


def test_admin_channel_separate(isolated):
    db.get_admin_connection()
    assert isolated[0][0]['user']=='h4u'


def test_migrator_rejects_runtime_identity(isolated,monkeypatch):
    monkeypatch.setenv('DB_USER','h4u_runtime')
    with pytest.raises(RuntimeError):db.get_admin_connection()
    assert not isolated[0]


def test_rejects_privileged_runtime(isolated,monkeypatch):
    monkeypatch.setenv('DB_RUNTIME_USER','h4u_runtime')
    monkeypatch.setenv('DB_RUNTIME_PASSWORD','synthetic-runtime')
    monkeypatch.setattr(isolated[1],'fetchone',lambda:(True,False,False,False,False))
    with pytest.raises(RuntimeError):db.get_connection()
    assert isolated[1].closed


def test_013_needs_exact_authorization():
    from scripts.apply_offer_consent import run_h4u
    with pytest.raises(RuntimeError):run_h4u('wrong',persist=True)
