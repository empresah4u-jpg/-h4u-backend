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


def test_rehearsal_mismatch_rolls_back_before_commit(storage, monkeypatch):
    data = manifest(storage)
    before = reconcile.snapshot(storage)
    original = reconcile.insert_links
    calls = []
    def divergent(conn, reviewed):
        result = original(conn, reviewed)
        calls.append(True)
        if len(calls) == 2:
            return [{**result[0], 'code':'different'}]
        return result
    monkeypatch.setattr(reconcile, 'insert_links', divergent)
    with pytest.raises(ValueError, match='Rehearsal mismatch'):
        reconcile.reconcile(storage, data, apply=True)
    assert reconcile.snapshot(storage) == before


def test_existing_provenance_drift_is_not_silently_accepted(storage):
    data = manifest(storage)
    reconcile.reconcile(storage, data, apply=True)
    storage.execute("UPDATE entity_sources SET notes='Unexpected evidence'")
    before = reconcile.snapshot(storage)
    with pytest.raises(ValueError, match='Existing provenance drift'):
        reconcile.reconcile(storage, data, apply=True)
    assert reconcile.snapshot(storage) == before


def migration(conn):
    from scripts.apply_catalog_integrity import ROOT, NAME
    conn.execute((ROOT/NAME).read_text())


def add_embedding(conn, entity, digest='first'):
    conn.execute('''INSERT INTO entity_embeddings(destination_id,entity_type,entity_id,content,content_hash)
        VALUES (%s,'hotel',%s,'Synthetic content',%s)''', (uuid4(),entity,digest))


def test_database_rejects_duplicate_source_key(storage):
    from psycopg.errors import UniqueViolation
    data = manifest(storage)
    migration(storage)
    reconcile.reconcile(storage,data,apply=True)
    before = reconcile.snapshot(storage)
    with pytest.raises(UniqueViolation):
        storage.execute('INSERT INTO entity_sources(source_id,entity_type,entity_id) SELECT source_id,entity_type,entity_id FROM entity_sources')
    assert reconcile.snapshot(storage) == before


@pytest.mark.parametrize('digest',['first','different'])
def test_database_rejects_duplicate_embedding_key(storage,digest):
    from psycopg.errors import UniqueViolation
    migration(storage)
    entity = uuid4()
    add_embedding(storage,entity)
    with pytest.raises(UniqueViolation):
        add_embedding(storage,entity,digest)
    assert storage.execute('SELECT count(*) FROM entity_embeddings').fetchone() == (1,)


def test_constraints_allow_distinct_relationships_and_entities(storage):
    migration(storage)
    source1,source2,entity1,entity2 = [uuid4() for _ in range(4)]
    for source,entity in [(source1,entity1),(source2,entity1),(source1,entity2)]:
        storage.execute("INSERT INTO entity_sources(source_id,entity_type,entity_id) VALUES (%s,'hotel',%s)",(source,entity))
    for entity in (entity1,entity2):
        add_embedding(storage,entity)
    assert storage.execute('SELECT count(*) FROM entity_sources').fetchone() == (3,)
    assert storage.execute('SELECT count(*) FROM entity_embeddings').fetchone() == (2,)


def test_migration_duplicate_failure_rolls_back_all_ddl(storage):
    from psycopg.errors import UniqueViolation
    from scripts.apply_catalog_integrity import CONSTRAINTS
    entity = uuid4()
    add_embedding(storage,entity)
    add_embedding(storage,entity,'second')
    before = reconcile.snapshot(storage)
    with pytest.raises(UniqueViolation):
        with storage.transaction():
            migration(storage)
    assert reconcile.snapshot(storage) == before
    assert storage.execute("SELECT count(*) FROM pg_constraint WHERE conrelid='entity_sources'::regclass AND conname=%s",(next(iter(CONSTRAINTS)),)).fetchone() == (0,)


def test_migration_runner_rehearsal_application_and_repeat(storage):
    from scripts import apply_catalog_integrity as runner
    from hashlib import sha256
    storage.execute('CREATE TEMP TABLE schema_migrations (LIKE public.schema_migrations INCLUDING ALL)')
    for path in runner.ROOT.glob('00[2-8]_*.sql'):
        storage.execute('INSERT INTO schema_migrations(version,checksum) VALUES (%s,%s)',(path.name,sha256(path.read_bytes()).hexdigest()))
    before = runner.schema(storage),reconcile.snapshot(storage)
    runner.run(storage)
    assert (runner.schema(storage),reconcile.snapshot(storage)) == before
    runner.run(storage,True)
    runner.verify(storage)
    after = runner.schema(storage),reconcile.snapshot(storage)
    runner.run(storage,True)
    assert (runner.schema(storage),reconcile.snapshot(storage)) == after


def test_migration_runner_refuses_checksum_drift(storage):
    from scripts import apply_catalog_integrity as runner
    storage.execute('CREATE TEMP TABLE schema_migrations (LIKE public.schema_migrations INCLUDING ALL)')
    with pytest.raises(RuntimeError,match='checksum'):
        runner.run(storage,True)
    assert storage.execute('SELECT count(*) FROM schema_migrations').fetchone() == (0,)


def reviewed_url(conn):
    data = manifest(conn)
    reconcile.reconcile(conn,data,apply=True)
    conn.execute('UPDATE entity_sources SET source_url=NULL')
    ident=conn.execute('SELECT id FROM entity_sources').fetchone()[0]
    source=data['sources'][0]
    link=source['links'][0]
    return [dict(id=str(ident),source_id=source['id'],source_name=source['name'],
                 url=source['url'],**link)]


def test_url_rehearsal_and_apply_preserve_all_other_data(storage):
    from scripts.normalize_provenance_urls import normalize
    reviewed=reviewed_url(storage)
    before=reconcile.snapshot(storage)
    assert len(normalize(storage,reviewed)['changed'])==1
    assert reconcile.snapshot(storage)==before
    assert len(normalize(storage,reviewed,True)['changed'])==1
    after=reconcile.snapshot(storage)
    assert not normalize(storage,reviewed,True)['changed']
    assert reconcile.snapshot(storage)==after


def test_url_drift_rolls_back_prior_url_updates(storage):
    from scripts.normalize_provenance_urls import normalize
    reviewed=reviewed_url(storage)
    invalid={**reviewed[0],'id':str(uuid4())}
    before=reconcile.snapshot(storage)
    with pytest.raises(ValueError,match='drift'):
        normalize(storage,reviewed+[invalid],True)
    assert reconcile.snapshot(storage)==before


def test_url_does_not_overwrite_existing_different_url(storage):
    from scripts.normalize_provenance_urls import normalize
    reviewed=reviewed_url(storage)
    storage.execute("UPDATE entity_sources SET source_url='https://example.test/other'")
    before=reconcile.snapshot(storage)
    with pytest.raises(ValueError,match='existing URL'):
        normalize(storage,reviewed,True)
    assert reconcile.snapshot(storage)==before
