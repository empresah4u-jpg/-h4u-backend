"""Synthetic evidence; DDL is tested only in isolated demo and always rolled back."""
from copy import deepcopy
from pathlib import Path
from uuid import uuid4
import subprocess
import pytest
from pipelines.load_hotels import preview
from tests.test_hotel_preview import record, state, SOURCE, HOTEL


def linked():
    catalog=state(True)
    catalog['links']=[dict(id=SOURCE,source_id=SOURCE,entity_id=HOTEL,
        source_url='https://example.invalid/',notes='Synthetic evidence')]
    return catalog


def result(data=None,catalog=None):
    return preview([data or record()],catalog or linked())['records'][0]


def test_same_legacy_evidence():
    r=result()
    assert (r['catalog_action'],r['provenance_action'])==('UNCHANGED','UNCHANGED')


def test_compatible_addition_preserves_input_and_existing_evidence():
    c=linked();before=deepcopy(c);data=record();data['metadata']['note']='Additional historical observation'
    r=result(data,c)
    assert (r['catalog_action'],r['provenance_action'])==('UNCHANGED','ADD')
    assert c==before
    assert result(data,c)==r


def test_alternate_url_is_review_not_contradiction():
    data=record();data['source_url']='https://example.invalid/historical'
    r=result(data)
    assert (r['catalog_action'],r['provenance_action'])==('UNCHANGED','REVIEW')


def test_protected_catalog_contradiction():
    c=linked();c['hotels'][0]['verification_status']='verified'
    r=result(record(phone='456'),c)
    assert (r['catalog_action'],r['provenance_action'])==('CONFLICT','CONFLICT')


def test_unresolved_source_rejected():
    c=linked();c['sources']=[]
    r=result(catalog=c)
    assert (r['catalog_action'],r['provenance_action'])==('REJECT','REVIEW')


def test_source_identity_contradiction():
    r=result(record(source='Different source'))
    assert (r['catalog_action'],r['provenance_action'])==('REJECT','CONFLICT')


@pytest.mark.parametrize('status,expected',[('unverified','UNCHANGED'),('verified','UNCHANGED'),('rejected','CONFLICT'),('disputed','REVIEW')])
def test_child_evidence_matching(status,expected):
    c=linked();c['evidence']=[dict(entity_source_id=SOURCE,source_url='https://example.invalid/',
        notes='Synthetic evidence',source_reference='doc:1',verification_status=status)]
    data=record();data['metadata']['source_reference']='doc:1'
    assert result(data,c)['provenance_action']==expected


def test_same_document_changed_observation_needs_review():
    c=linked();c['evidence']=[dict(entity_source_id=SOURCE,source_url='https://example.invalid/',
        notes='Earlier observation',source_reference='doc:1')]
    data=record();data['metadata']['source_reference']='doc:1'
    assert result(data,c)['provenance_action']=='REVIEW'


def test_unknown_observation_time_not_equivalent_to_dated_observation():
    c=linked();c['evidence']=[dict(entity_source_id=SOURCE,source_url='https://example.invalid/',
        notes='Synthetic evidence',source_reference='doc:1',observed_at='2026-08-21T00:00:00Z')]
    data=record();data['metadata'].update(source_reference='doc:1',observed_at='2026-08-20T19:00:00-05:00')
    assert result(data,c)['provenance_action']=='UNCHANGED'
    data['metadata']['observed_at']='2026-08-21'
    assert result(data,c)['catalog_action']=='REJECT'


@pytest.mark.parametrize('code',['HOT053','HOT071','HOT065'])
def test_pending_historical_identities_never_create_or_merge(code):
    c=state();c['destinations'][0]['code']='PARACAS'
    data=record();data.update(code=code,destination='PARACAS')
    r=result(data,c)
    assert r['catalog_action']=='CONFLICT' and r['provenance_action']=='REVIEW'


def test_migration_constraints_append_only_multiple_evidence_and_rollback():
    # Explicit fixed demo container/database. A transaction-local schema contains only fictitious rows.
    schema='evidence_test_'+uuid4().hex
    migration=Path('db/migrations/010_entity_source_evidence.sql').read_text()
    query=f'''BEGIN;
SET LOCAL lock_timeout='5s';
CREATE SCHEMA {schema}; SET LOCAL search_path={schema},pg_catalog;
CREATE TABLE entity_sources(id uuid PRIMARY KEY, notes text);
INSERT INTO entity_sources VALUES ('{SOURCE}','Original preserved evidence');
{migration}
INSERT INTO entity_source_evidence(entity_source_id,source_url,notes)
VALUES ('{SOURCE}','https://example.invalid/','First'),('{SOURCE}','https://example.invalid/','Second');
INSERT INTO entity_source_evidence(entity_source_id,source_reference,notes,observed_at)
VALUES ('{SOURCE}','document:1','Dated','2026-08-21T00:00:00Z');
SET LOCAL TIME ZONE 'America/Lima';
DO $$ BEGIN
 BEGIN
  INSERT INTO entity_source_evidence(entity_source_id,source_reference,notes,observed_at)
  VALUES ('{SOURCE}','document:1','Dated','2026-08-20T19:00:00-05:00');
  RAISE EXCEPTION 'Equivalent instant duplicated';
 EXCEPTION WHEN unique_violation THEN NULL; END;
 BEGIN
  INSERT INTO entity_source_evidence(entity_source_id,source_url,notes,verification_status)
  VALUES ('{SOURCE}','https://example.invalid/','Bad verification','invented');
  RAISE EXCEPTION 'Invalid status accepted';
 EXCEPTION WHEN check_violation THEN NULL; END;
 IF (SELECT count(*) FROM entity_source_evidence) <> 3 THEN RAISE EXCEPTION 'Multiple evidence failed'; END IF;
 IF (SELECT notes FROM entity_sources) <> 'Original preserved evidence' THEN RAISE EXCEPTION 'Parent changed'; END IF;
 BEGIN
  INSERT INTO entity_source_evidence(entity_source_id,source_url,notes,fingerprint)
  VALUES ('{SOURCE}','https://example.invalid/','First',repeat('a',64));
  RAISE EXCEPTION 'Duplicate accepted';
 EXCEPTION WHEN unique_violation THEN NULL; END;
 BEGIN
  INSERT INTO entity_source_evidence(entity_source_id,source_url,notes)
  VALUES ('{HOTEL}','https://example.invalid/','Orphan');
  RAISE EXCEPTION 'Orphan accepted';
 EXCEPTION WHEN foreign_key_violation THEN NULL; END;
 BEGIN
  UPDATE entity_source_evidence SET notes='Overwrite';
  RAISE EXCEPTION 'Update accepted';
 EXCEPTION WHEN raise_exception THEN
  IF SQLERRM NOT LIKE 'Evidence is append-only%' THEN RAISE; END IF;
 END;
 BEGIN
  DELETE FROM entity_source_evidence;
  RAISE EXCEPTION 'Delete accepted';
 EXCEPTION WHEN raise_exception THEN
  IF SQLERRM NOT LIKE 'Evidence is append-only%' THEN RAISE; END IF;
 END;
 BEGIN
  DELETE FROM entity_sources;
  RAISE EXCEPTION 'Parent deleted';
 EXCEPTION WHEN foreign_key_violation THEN NULL; END;
END $$;
ROLLBACK;
SELECT count(*) FROM pg_namespace WHERE nspname='{schema}';'''
    run=subprocess.run(['docker','exec','-i','h4u-demo-postgres','psql','-X','-U','h4u_demo_setup','-d','h4u_demo','-At','-v','ON_ERROR_STOP=1'],input=query,text=True,capture_output=True,timeout=30)
    assert run.returncode==0,run.stderr
    assert run.stdout.strip().splitlines()[-1]=='0'


def test_verified_child_evidence_protects_catalog():
    c=linked();c['evidence']=[dict(entity_source_id=SOURCE,verification_status='verified',
        source_url='https://example.invalid/',notes='Prior observation')]
    r=result(record(phone='456'),c)
    assert (r['catalog_action'],r['provenance_action'])==('CONFLICT','CONFLICT')


def test_distinct_references_are_distinct_evidence():
    c=linked();c['evidence']=[dict(entity_source_id=SOURCE,source_url='https://example.invalid/',
        notes='Synthetic evidence',source_reference='doc:1')]
    data=record();data['metadata']['source_reference']='doc:2'
    assert result(data,c)['provenance_action']=='ADD'
