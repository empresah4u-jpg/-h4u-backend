"""Real pgvector/transactions, exclusively session-local TEMP tables.

No public entity or embedding rows are inserted, updated, deleted or copied.
The encoder is deterministic so tests do not download or rebuild a model.
"""
from uuid import uuid4

import pytest
from psycopg import sql
from app.db import get_connection
from app.embeddings import MODEL_NAME, DIMENSIONS, vector_to_pg
from scripts import sync_embeddings as sync
from scripts.generate_embeddings import load_entities, content_hash

VECTOR = [1.0] + [0.0] * (DIMENSIONS - 1)


@pytest.fixture
def storage(monkeypatch):
    with get_connection() as conn:
        conn.autocommit = True
        for table in ('hotels','restaurants','tours','attractions','entity_embeddings'):
            conn.execute(sql.SQL('CREATE TEMP TABLE {} (LIKE public.{} INCLUDING DEFAULTS INCLUDING CONSTRAINTS)').format(sql.Identifier(table),sql.Identifier(table)))
        assert conn.execute("SELECT relnamespace=pg_my_temp_schema() FROM pg_class WHERE oid='entity_embeddings'::regclass").fetchone()==(True,)
        class Encoder:
            calls=[]
            def encode(self,text,*,normalize_embeddings):
                assert normalize_embeddings
                self.calls.append(text)
                return VECTOR
        model=Encoder()
        monkeypatch.setattr(sync,'get_model',lambda:model)
        yield conn,model


def seed(conn,table='hotels',status='active'):
    ident,destination=uuid4(),uuid4()
    conn.execute(sql.SQL('INSERT INTO {}(id,destination_id,code,name,status) VALUES (%s,%s,%s,%s,%s)').format(sql.Identifier(table)),
                 (ident,destination,uuid4().hex[:16],'Canonical test',status))
    with conn.cursor() as cur:
        entities=load_entities(cur)
    return next((e for e in entities if e['entity_id']==ident),None)


def embedding(conn,entity,**changes):
    data=dict(destination_id=entity['destination_id'],entity_type=entity['entity_type'],entity_id=entity['entity_id'],
              content=entity['content'],embedding=vector_to_pg(VECTOR),embedding_model=MODEL_NAME,content_hash=content_hash(entity['content']))
    data.update(changes)
    return conn.execute('''INSERT INTO entity_embeddings(destination_id,entity_type,entity_id,content,embedding,embedding_model,content_hash)
        VALUES (%s,%s,%s,%s,%s::vector,%s,%s) RETURNING id''',tuple(data.values())).fetchone()[0]


def snapshot(conn):
    return conn.execute('SELECT id,destination_id,entity_type,entity_id,content,embedding::text,embedding_model,content_hash,created_at,updated_at,xmin::text,ctid::text FROM entity_embeddings ORDER BY id').fetchall()


def test_inspection_is_read_only_and_never_loads_model(storage,monkeypatch):
    conn,_=storage
    entity=seed(conn); embedding(conn,entity,embedding_model='wrong')
    seed(conn)
    before=snapshot(conn)
    original=sync.plan
    def plan(cur):
        cur.execute('SHOW transaction_read_only')
        assert cur.fetchone()==('on',)
        return original(cur)
    monkeypatch.setattr(sync,'plan',plan)
    monkeypatch.setattr(sync,'get_model',lambda:pytest.fail('Inspection loaded a model'))
    result=sync.synchronize(conn)
    assert (result['create'],result['update'],result['written'])==(1,1,0)
    assert snapshot(conn)==before


@pytest.mark.parametrize('table',['hotels','restaurants','tours','attractions'])
def test_create_uses_exact_canonical_content(storage,table):
    conn,model=storage; entity=seed(conn,table)
    result=sync.synchronize(conn,apply=True)
    assert result['create']==1 and result['written']==1
    row=conn.execute('SELECT destination_id,entity_type,entity_id,content,embedding_model,content_hash,vector_dims(embedding) FROM entity_embeddings').fetchone()
    assert row==(entity['destination_id'],entity['entity_type'],entity['entity_id'],entity['content'],MODEL_NAME,content_hash(entity['content']),384)
    assert model.calls==[entity['content']]


@pytest.mark.parametrize('changes,reason',[
    ({'content_hash':'old'},'content_hash'),
    ({'embedding_model':'wrong'},'embedding_model'),
    ({'embedding':None},'vector_contract'),
    ({'embedding':vector_to_pg([0.0]*384)},'vector_contract'),
    ({'embedding':vector_to_pg([2.0]+[0.0]*383)},'vector_contract'),
    ({'destination_id':uuid4()},'destination_id'),
    ({'content':'stale despite matching hash'},'canonical_content'),
])
def test_update_preserves_id_and_created_at(storage,changes,reason):
    conn,_=storage; entity=seed(conn); identifier=embedding(conn,entity,**changes)
    created=conn.execute('SELECT created_at FROM entity_embeddings WHERE id=%s',(identifier,)).fetchone()[0]
    result=sync.synchronize(conn,apply=True)
    assert result['update']==1 and result['update_reasons'][reason]==1
    assert conn.execute('SELECT id,created_at FROM entity_embeddings').fetchone()==(identifier,created)
    assert sync.synchronize(conn)['unchanged']==1


def test_canonical_source_change_updates_hash(storage):
    conn,_=storage; entity=seed(conn); embedding(conn,entity)
    conn.execute("UPDATE hotels SET name='Changed name' WHERE id=%s",(entity['entity_id'],))
    result=sync.synchronize(conn,apply=True)
    assert result['update']==1 and result['update_reasons']['content_hash']==1
    with conn.cursor() as cur: expected=load_entities(cur)[0]['content']
    assert conn.execute('SELECT content,content_hash FROM entity_embeddings').fetchone()==(expected,content_hash(expected))


def test_unchanged_and_second_apply_do_not_write(storage,monkeypatch):
    conn,model=storage; seed(conn)
    sync.synchronize(conn,apply=True)
    before=snapshot(conn)
    monkeypatch.setattr(sync,'get_model',lambda:pytest.fail('Unchanged rows loaded model'))
    result=sync.synchronize(conn,apply=True)
    assert (result['create'],result['update'],result['unchanged'],result['written'])==(0,0,1,0)
    assert snapshot(conn)==before
    assert len(model.calls)==1


def test_orphans_missing_and_inactive_are_only_reported(storage):
    conn,_=storage; entity=seed(conn); first=embedding(conn,entity)
    conn.execute("UPDATE hotels SET status='inactive' WHERE id=%s",(entity['entity_id'],))
    second=embedding(conn,{**entity,'entity_id':uuid4()})
    before=snapshot(conn)
    result=sync.synchronize(conn,apply=True)
    assert result['orphan']==2 and result['written']==0
    assert {r['id'] for r in result['orphan_records']}=={str(first),str(second)}
    assert snapshot(conn)==before


@pytest.mark.parametrize('failure',['encoder','dimension','nonfinite','sql'])
def test_error_rolls_back_previous_writes(storage,monkeypatch,failure):
    conn,_=storage
    seed(conn); seed(conn)
    before=snapshot(conn)
    calls=[]
    class Encoder:
        def encode(self,text,**kwargs):
            calls.append(text)
            if len(calls)==2:
                if failure=='encoder': raise RuntimeError('Injected failure')
                if failure=='dimension': return [0.0]*383
                if failure=='nonfinite': return [float('nan')]*384
                # Constraint error on the second INSERT after the first succeeded.
                return [-1.0]+[0.0]*383
            return VECTOR
    if failure=='sql':
        conn.execute("ALTER TABLE entity_embeddings ADD CONSTRAINT test_embedding_sign CHECK(embedding::text NOT LIKE '[-1,%%')")
    monkeypatch.setattr(sync,'get_model',lambda:Encoder())
    with pytest.raises(Exception): sync.synchronize(conn,apply=True)
    assert len(calls)==2 and snapshot(conn)==before


def test_duplicate_keys_reported_and_apply_refused(storage):
    conn,_=storage; entity=seed(conn)
    embedding(conn,entity); embedding(conn,entity)
    before=snapshot(conn)
    report=sync.synchronize(conn)
    assert len(report['unique']['duplicate_groups'])==1 and report['blocked_entities']==1
    with pytest.raises(RuntimeError,match='Duplicate'): sync.synchronize(conn,apply=True)
    assert snapshot(conn)==before


def test_storage_dimension_mismatch_fails_without_writes(storage):
    conn,_=storage; seed(conn)
    conn.execute('ALTER TABLE entity_embeddings ALTER COLUMN embedding TYPE vector(383)')
    with pytest.raises(RuntimeError,match='vector'): sync.synchronize(conn,apply=True)
    assert snapshot(conn)==[]


def test_unknown_entity_types_are_not_deleted_or_called_orphans(storage):
    conn,_=storage; entity=seed(conn)
    embedding(conn,{**entity,'entity_type':'other_catalog'})
    result=sync.synchronize(conn)
    assert result['orphan']==0 and len(result['unsupported_records'])==1
