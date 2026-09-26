"""PostgreSQL integration, session-local TEMP catalogs; no persistent writes."""
from copy import deepcopy
from uuid import uuid4

import pytest
from psycopg import sql
from app.db import get_connection
from scripts import reconcile_sources as reconcile


@pytest.fixture
def storage():
    with get_connection() as conn:
        conn.autocommit = True
        for table in (*reconcile.PROTECTED, 'entity_sources'):
            conn.execute(sql.SQL('CREATE TEMP TABLE {} (LIKE public.{} INCLUDING DEFAULTS INCLUDING CONSTRAINTS)')
                         .format(sql.Identifier(table), sql.Identifier(table)))
        assert conn.execute("SELECT relnamespace=pg_my_temp_schema() FROM pg_class WHERE oid='entity_sources'::regclass").fetchone() == (True,)
        yield conn


def manifest(conn, kind='hotel'):
    source_id, entity_id = uuid4(), uuid4()
    conn.execute("INSERT INTO data_sources(id,name,url,status) VALUES (%s,'Reviewed source','https://example.test/evidence','active')", (source_id,))
    conn.execute(sql.SQL("INSERT INTO {}(id,destination_id,code,name,status) VALUES (%s,%s,'TEST','Reviewed entity','active')")
                 .format(sql.Identifier(reconcile.TABLES[kind])), (entity_id, uuid4()))
    return {'reviewed_at':'2026-09-26T00:00:00+00:00', 'sources':[{
        'id':str(source_id), 'name':'Reviewed source', 'url':'https://example.test/evidence',
        'links':[{'entity_type':kind, 'entity_id':str(entity_id), 'code':'TEST',
                  'name':'Reviewed entity', 'evidence':'Explicit entity identification on source page.'}]}]}


def test_dry_run_inserts_then_verifies_full_rollback(storage):
    data = manifest(storage)
    before = reconcile.snapshot(storage)
    result = reconcile.reconcile(storage, data)
    assert len(result['added']) == 1 and result['rollback_verified']
    assert result['after']['entity_sources'] == 1
    assert reconcile.snapshot(storage) == before


@pytest.mark.parametrize('kind', ['hotel', 'general_service', 'emergency_service'])
def test_apply_is_idempotent_and_preserves_catalog(storage, kind):
    data = manifest(storage, kind)
    before = reconcile.snapshot(storage)
    result = reconcile.reconcile(storage, data, apply=True)
    assert len(result['added']) == 1
    after = reconcile.snapshot(storage)
    assert all(before[t] == after[t] for t in reconcile.PROTECTED)
    assert not reconcile.reconcile(storage, data, apply=True)['added']
    assert reconcile.snapshot(storage) == after
    assert storage.execute('SELECT confidence_score,notes FROM entity_sources').fetchone() == (None,data['sources'][0]['links'][0]['evidence'])


@pytest.mark.parametrize('field,value', [('name','Wrong entity'), ('entity_id',str(uuid4())),
                                          ('entity_type','invented'), ('evidence','')])
def test_failure_after_first_insert_rolls_back_everything(storage, field, value):
    data = manifest(storage)
    invalid = deepcopy(data['sources'][0]['links'][0])
    invalid[field] = value
    data['sources'][0]['links'].append(invalid)
    before = reconcile.snapshot(storage)
    with pytest.raises(ValueError):
        reconcile.reconcile(storage, data, apply=True)
    assert reconcile.snapshot(storage) == before


def test_source_drift_fails_closed(storage):
    data = manifest(storage)
    data['sources'][0]['url'] = 'https://example.test/other'
    with pytest.raises(ValueError, match='Source drift'):
        reconcile.reconcile(storage, data, apply=True)
    assert storage.execute('SELECT count(*) FROM entity_sources').fetchone() == (0,)


def test_orphans_and_duplicates_are_not_hidden(storage):
    data = manifest(storage)
    reconcile.reconcile(storage, data, apply=True)
    storage.execute('INSERT INTO entity_sources(source_id,entity_type,entity_id) SELECT source_id,entity_type,entity_id FROM entity_sources')
    with pytest.raises(ValueError, match='Integrity'):
        reconcile.reconcile(storage, data, apply=True)


def test_missing_polymorphic_entity_blocks_apply(storage):
    data = manifest(storage)
    storage.execute("INSERT INTO entity_sources(source_id,entity_type,entity_id) VALUES (%s,'hotel',%s)",
                    (data['sources'][0]['id'], uuid4()))
    with pytest.raises(ValueError, match='Integrity'):
        reconcile.reconcile(storage, data, apply=True)


def test_failure_in_real_apply_rolls_back(storage, monkeypatch):
    data = manifest(storage)
    before = reconcile.snapshot(storage)
    original = reconcile.insert_links
    calls = []
    def failing(conn, reviewed):
        result = original(conn, reviewed)
        calls.append(True)
        if len(calls) == 2:
            raise RuntimeError('Injected error after real insert')
        return result
    monkeypatch.setattr(reconcile, 'insert_links', failing)
    with pytest.raises(RuntimeError, match='Injected'):
        reconcile.reconcile(storage, data, apply=True)
    assert reconcile.snapshot(storage) == before
