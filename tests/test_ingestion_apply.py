"""Real transactions in disposable schemas of the isolated synthetic test DB only."""
from copy import deepcopy
from pathlib import Path
from uuid import uuid4
import os
import pytest
from psycopg import sql
from app.db import get_connection
from pipelines.ingestion_plan import create,validate,InvalidPlan,StalePlan,fingerprint
from pipelines.apply_hotels import apply
from scripts.process_ingestion_embeddings import process_one
from tests.test_hotel_preview import record,SOURCE,DEST


@pytest.fixture
def storage():
    assert os.environ.get('DB_NAME','').startswith('h4u_ingestion_test_'), 'Use scripts.test_ingestion_isolated; never write test data to H4U'
    with get_connection() as conn:
        conn.autocommit=True
        schema='fixture_'+uuid4().hex
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            conn.execute(sql.SQL('SET search_path={},public').format(sql.Identifier(schema)))
            for table in ('hotels','destinations','data_sources','entity_sources','entity_embeddings'):
                conn.execute(sql.SQL('CREATE TABLE {} (LIKE public.{} INCLUDING ALL)').format(sql.Identifier(table),sql.Identifier(table)))
            conn.execute(Path('db/migrations/010_entity_source_evidence.sql').read_text())
            # Bind the same constant qualified lock to this disposable fixture schema.
            migration=Path('db/migrations/011_ingestion_apply.sql').read_text().replace('public.entity_source_evidence',sql.Identifier(schema).as_string(conn)+'.entity_source_evidence')
            conn.execute(migration)
            conn.execute("INSERT INTO destinations(id,code,name,slug) VALUES (%s,'FICTITIOUS','Fictional','fictional')",(DEST,))
            conn.execute("INSERT INTO data_sources(id,name,source_type,url,status) VALUES (%s,'Fictional source','official','https://example.invalid/','active')",(SOURCE,))
            yield conn
        finally:
            conn.execute('SET search_path=public')
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def existing(conn,**fields):
    result=apply(conn,create(conn,[record()]),dry_run=False)
    identifier=result['result']['entities'][0]['entity_id']
    if fields:
        conn.execute(sql.SQL('UPDATE hotels SET {} WHERE id=%s').format(sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(k)) for k in fields)),(*fields.values(),identifier))
    return identifier


def counts(conn):
    return tuple(conn.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(t))).fetchone()[0] for t in ('hotels','entity_sources','entity_source_evidence','ingestion_plans','ingestion_embedding_jobs','entity_embeddings'))


class Encoder:
    calls=0
    def encode(self,text,**kwargs):
        self.calls+=1
        return [1.0]+[0.0]*383


def test_create_atomic(storage):
    plan=create(storage,[record()]);r=apply(storage,plan,dry_run=False)
    assert r['result']['embedding_jobs']==1
    assert counts(storage)==(1,1,1,1,1,0)
    assert storage.execute('SELECT status FROM ingestion_plans').fetchone()==('applied',)


def test_update_preserves_legacy_and_evidence(storage):
    existing(storage,phone='123')
    storage.execute("UPDATE entity_sources SET notes='Historical evidence'")
    plan=create(storage,[record(phone='456')]);before=storage.execute('SELECT to_jsonb(e) FROM entity_source_evidence e').fetchall()
    assert plan['entries'][0]['catalog_action']=='UPDATE'
    r=apply(storage,plan,dry_run=False)
    assert r['result']['embedding_jobs']==0
    assert storage.execute('SELECT phone FROM hotels').fetchone()==('456',)
    assert storage.execute('SELECT notes FROM entity_sources').fetchone()==('Historical evidence',)
    assert storage.execute('SELECT to_jsonb(e) FROM entity_source_evidence e').fetchall()==before


def test_unchanged_and_duplicate_evidence(storage):
    existing(storage)
    plan=create(storage,[record()]);assert plan['entries'][0]['provenance_action']=='UNCHANGED'
    apply(storage,plan,dry_run=False)
    assert counts(storage)==(1,1,1,2,1,0)


def test_additional_evidence(storage):
    existing(storage);row=record();row['metadata']['note']='Additional observation'
    plan=create(storage,[row]);assert plan['entries'][0]['catalog_action']=='UNCHANGED'
    apply(storage,plan,dry_run=False)
    assert counts(storage)==(1,1,2,2,1,0)


def test_replay_returns_durable_result_even_after_later_change(storage):
    plan=create(storage,[record()]);first=apply(storage,plan,dry_run=False);before=counts(storage)
    storage.execute("UPDATE hotels SET phone='789'")
    second=apply(storage,plan,dry_run=False)
    assert second['idempotent'] and second['result']==first['result'] and counts(storage)==before


def test_same_id_different_fingerprint_rejected(storage):
    p=create(storage,[record()]);apply(storage,p,dry_run=False)
    other=create(storage,[record(phone='789')],p['plan_id'])
    with pytest.raises(InvalidPlan,match='reused'):apply(storage,other,dry_run=False)


def test_same_content_different_id_returns_original_receipt(storage):
    p=create(storage,[record()]);other=deepcopy(p);other['plan_id']=str(uuid4())
    first=apply(storage,p,dry_run=False);second=apply(storage,other,dry_run=False)
    assert second['idempotent'] and first['result']==second['result']
    assert counts(storage)[3]==1


@pytest.mark.parametrize('target',['hotels','entity_sources','data_sources','destinations'])
def test_stale(storage,target):
    existing(storage);p=create(storage,[record(phone='456')]);before=counts(storage)
    if target=='hotels':storage.execute("UPDATE hotels SET phone='999'")
    elif target=='entity_sources':storage.execute("UPDATE entity_sources SET notes='Another observer'")
    else:storage.execute(f"UPDATE {target} SET status='inactive'")
    with pytest.raises(StalePlan):apply(storage,p,dry_run=False)
    assert counts(storage)==before


@pytest.mark.parametrize('action',['CONFLICT','REVIEW','REJECT','AMBIGUOUS'])
def test_forbidden_action_rejected(storage,action):
    p=create(storage,[record()]);p['entries'][0]['catalog_action']=action
    # Even recomputing the public hash cannot bypass replayed preview validation.
    body={k:v for k,v in p.items() if k not in ('plan_id','plan_fingerprint')};p['plan_fingerprint']=fingerprint(body)
    with pytest.raises(InvalidPlan):apply(storage,p,dry_run=False)
    assert counts(storage)==(0,0,0,0,0,0)


def test_provenance_review_rejected(storage):
    p=create(storage,[record()]);p['entries'][0]['provenance_action']='REVIEW'
    with pytest.raises(InvalidPlan):apply(storage,p,dry_run=False)


def test_failure_after_hotel_insert_rolls_back_entire_batch(storage):
    p=create(storage,[record()]);storage.execute("ALTER TABLE entity_source_evidence ADD CONSTRAINT test_failure CHECK (notes='never matches')")
    with pytest.raises(Exception):apply(storage,p,dry_run=False)
    assert counts(storage)==(0,0,0,0,0,0)


def test_semantic_update_queues_job(storage):
    existing(storage);r=apply(storage,create(storage,[record(address='New fictional address')]),dry_run=False)
    assert r['result']['embedding_jobs']==1 and counts(storage)[4]==2


def test_nonsemantic_change_does_not_queue(storage):
    existing(storage);r=apply(storage,create(storage,[record(phone='123')]),dry_run=False)
    assert r['result']['embedding_jobs']==0 and counts(storage)[4]==1


def test_worker_and_matching_embedding_skip(storage):
    existing(storage);encoder=Encoder()
    first=process_one(storage,model_factory=lambda:encoder)
    assert first['status']=='completed' and encoder.calls==1
    assert process_one(storage,model_factory=lambda:encoder)['status']=='idle'
    # Another legitimate plan reactivates the same semantic content, with existing vector.
    storage.execute("UPDATE hotels SET status='inactive'")
    p=create(storage,[record(status='active')]);apply(storage,p,dry_run=False)
    second=process_one(storage,model_factory=lambda:encoder)
    assert second['status']=='completed' and not second['embedding_written'] and encoder.calls==1


def test_failed_job_retry_sanitized(storage):
    existing(storage)
    def fail():raise RuntimeError('private credential detail')
    result=process_one(storage,model_factory=fail)
    assert result['status']=='failed' and 'private' not in str(result)
    assert storage.execute('SELECT attempts,last_error FROM ingestion_embedding_jobs').fetchone()==(1,'embedding_processing_failed')
    assert process_one(storage,model_factory=Encoder)['status']=='completed'
    assert storage.execute('SELECT attempts FROM ingestion_embedding_jobs').fetchone()==(2,)


def test_abandoned_processing_recovers(storage):
    existing(storage)
    storage.execute("UPDATE ingestion_embedding_jobs SET status='processing',attempts=attempts+1")
    assert process_one(storage,model_factory=Encoder)['status']=='completed'


def test_dry_run_is_readonly_deterministic(storage):
    p=create(storage,[record()]);before=counts(storage)
    a=apply(storage,p);b=apply(storage,p,dry_run=True)
    assert a==b and counts(storage)==before and a['result']['embedding_jobs']==1
    # Database-enforced read-only session also succeeds, so no rollback-only writes.
    storage.execute('SET default_transaction_read_only=on')
    try:assert apply(storage,p)==a
    finally:storage.execute('SET default_transaction_read_only=off')


def test_plan_fingerprint_deterministic(storage):
    assert create(storage,[record()])==create(storage,[record()])
    with pytest.raises(InvalidPlan):apply(storage,[record()],dry_run=False)


def test_unfinished_plan_cannot_commit(storage):
    from psycopg.types.json import Jsonb
    p=create(storage,[record()])
    with pytest.raises(Exception):
        with storage.transaction():
            storage.execute("INSERT INTO ingestion_plans(id,contract_version,plan_fingerprint,entity_type,plan,status) VALUES (%s,1,%s,'hotel',%s,'applying')",(p['plan_id'],p['plan_fingerprint'],Jsonb(p)))
    assert counts(storage)[3]==0


def test_stale_worker_never_overwrites_newer_content(storage):
    existing(storage);storage.execute("UPDATE hotels SET name='Changed elsewhere'")
    assert process_one(storage,model_factory=Encoder)['status']=='failed'
    assert counts(storage)[5]==0


def test_batch_failure_is_not_partial(storage):
    one=record();two=record();two.update(code='FAKE002',name='Another fictional hotel')
    p=create(storage,[one,two]);storage.execute("ALTER TABLE hotels ADD CONSTRAINT test_failure CHECK (code <> 'FAKE002')")
    with pytest.raises(Exception):apply(storage,p,dry_run=False)
    assert counts(storage)==(0,0,0,0,0,0)


def test_numeric_canonical_format_matches_legacy_generator(storage):
    from scripts.generate_embeddings import hotel_content,content_hash
    identifier=existing(storage)
    storage.execute("UPDATE hotels SET rating=4.50,price_observed=100.00,currency='PEN' WHERE id=%s",(identifier,))
    p=create(storage,[record(address='Modified')]);apply(storage,p,dry_run=False)
    job=storage.execute('SELECT id,content_hash FROM ingestion_embedding_jobs WHERE ingestion_plan_id=%s',(p['plan_id'],)).fetchone()
    rating,price=storage.execute('SELECT rating,price_observed FROM hotels').fetchone()
    expected=content_hash(hotel_content(dict(name='Fictional Hotel',address='Modified',rating=rating,price_observed=price,currency='PEN')))
    assert job[1]==expected
    assert process_one(storage,job_id=job[0],model_factory=Encoder)['status']=='completed'
    assert storage.execute('SELECT content_hash FROM entity_embeddings').fetchone()==(expected,)


def test_concurrent_same_plan_writes_once(storage):
    from concurrent.futures import ThreadPoolExecutor
    p=create(storage,[record()]);schema=storage.execute('SELECT current_schema()').fetchone()[0]
    def run():
        with get_connection() as c:
            c.autocommit=True
            c.execute(sql.SQL('SET search_path={},public').format(sql.Identifier(schema)))
            return apply(c,p,dry_run=False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:run(),range(2)))
    assert sorted(r['idempotent'] for r in results)==[False,True]
    assert results[0]['result']==results[1]['result']
    assert counts(storage)==(1,1,1,1,1,0)


def test_worker_crash_rolls_processing_back(storage):
    existing(storage)
    class SimulatedCrash(BaseException):pass
    def crash():raise SimulatedCrash()
    with pytest.raises(SimulatedCrash):process_one(storage,model_factory=crash)
    assert storage.execute('SELECT status,attempts FROM ingestion_embedding_jobs').fetchone()==('pending',0)
    assert process_one(storage,model_factory=Encoder)['status']=='completed'


def test_new_evidence_after_preview_is_stale(storage):
    existing(storage);p=create(storage,[record(phone='456')])
    storage.execute("INSERT INTO entity_source_evidence(entity_source_id,source_url,notes) SELECT id,source_url,'Another observation' FROM entity_sources")
    before=counts(storage)
    with pytest.raises(StalePlan):apply(storage,p,dry_run=False)
    assert counts(storage)==before


def test_both_forbidden_fields_rejected_for_all_actions(storage):
    for field in ('catalog_action','provenance_action'):
        for action in ('CONFLICT','REVIEW','REJECT','AMBIGUOUS'):
            p=create(storage,[record()]);p['entries'][0][field]=action
            with pytest.raises(InvalidPlan):apply(storage,p,dry_run=False)
    assert counts(storage)==(0,0,0,0,0,0)


def test_completed_job_and_applied_plan_are_immutable(storage):
    existing(storage);process_one(storage,model_factory=Encoder)
    with pytest.raises(Exception):storage.execute("UPDATE ingestion_plans SET result='{}'")
    with pytest.raises(Exception):storage.execute("UPDATE ingestion_embedding_jobs SET status='processing',completed_at=NULL")


def test_pending_historical_codes_cannot_enter_plan(storage):
    storage.execute("UPDATE destinations SET code='PARACAS'")
    for code in ('HOT053','HOT071','HOT065'):
        data=record();data.update(code=code,destination='PARACAS')
        with pytest.raises(InvalidPlan):create(storage,[data])


def test_job_attempt_counter_cannot_move_backwards(storage):
    existing(storage)
    with pytest.raises(Exception):storage.execute("UPDATE ingestion_embedding_jobs SET status='processing',attempts=0")
    storage.execute("UPDATE ingestion_embedding_jobs SET status='processing',attempts=1")
    with pytest.raises(Exception):storage.execute("UPDATE ingestion_embedding_jobs SET status='processing',attempts=0")


def test_canonical_object_key_order(storage):
    original=record();reordered=dict(reversed(list(original.items())))
    assert create(storage,[original])==create(storage,[reordered])


def test_inactive_outcome_requires_review(storage):
    with pytest.raises(InvalidPlan):create(storage,[record(status='inactive')])


def test_boolean_contract_version_is_not_integer_version(storage):
    p=create(storage,[record()]);p['contract_version']=True
    with pytest.raises(InvalidPlan):apply(storage,p)
