"""010 deployment checks, no persistent catalog writes."""
import json
import subprocess
from uuid import uuid4
import pytest
from scripts import apply_source_evidence as migration


def previous():
    return {k:v for k,v in migration.checksums().items() if k != migration.NAME}


def test_prerequisite_drift_stops_before_ddl():
    ledger=previous();ledger['009_catalog_integrity.sql']='incorrect'
    with pytest.raises(RuntimeError,match='prerequisite checksum'):
        migration.migration_sql(ledger)


def test_applied_checksum_drift_stops():
    with pytest.raises(RuntimeError,match='010 checksum'):
        migration.migration_sql(dict(previous(),**{migration.NAME:'incorrect'}))


def test_idempotent_payload_does_not_recreate_or_register():
    payload=migration.migration_sql(migration.checksums())
    assert 'CREATE TABLE' not in payload
    assert 'INSERT INTO schema_migrations' not in payload
    assert 'Immutable trigger mismatch' in payload


def test_demo_permissions_exclude_mutations():
    payload=migration.migration_sql(previous(),demo=True)
    assert 'GRANT SELECT,INSERT ON entity_source_evidence TO h4u_demo_app' in payload
    assert 'REVOKE UPDATE,DELETE,TRUNCATE' in payload


def test_official_demo_upgrade_routes_010(monkeypatch):
    from scripts import setup_demo
    calls=[]
    monkeypatch.setattr(setup_demo,'check_marker',lambda:None)
    monkeypatch.setattr(setup_demo,'sql',lambda *args:json.dumps(previous()))
    monkeypatch.setattr(migration,'run_demo',lambda persist=False:calls.append(persist))
    setup_demo.upgrade_demo_schema(migration.checksums())
    assert calls==[True]


def test_deployment_payload_in_isolated_transaction():
    schema='deployment_010_'+uuid4().hex
    ledger=previous()
    sql=f'BEGIN; CREATE SCHEMA {schema}; SET LOCAL search_path={schema},pg_catalog;'
    for table in migration.TABLES:
        if table=='entity_sources':
            sql+='CREATE TABLE entity_sources(id uuid PRIMARY KEY,source_id uuid,entity_type text,entity_id uuid,CONSTRAINT entity_sources_source_entity_unique UNIQUE(source_id,entity_type,entity_id));'
        else:
            sql+=f'CREATE TABLE {table}(id uuid PRIMARY KEY);'
    sql+='CREATE TABLE schema_migrations(version text PRIMARY KEY,checksum text);'
    for name,checksum in ledger.items():
        sql+='INSERT INTO schema_migrations VALUES ('+migration.literal(name)+','+migration.literal(checksum)+');'
    sql+=migration.LOCKS+migration.migration_sql(ledger,demo=True)
    # A second execution checks idempotence and immutable-function introspection.
    sql+=migration.migration_sql(migration.checksums(),demo=True)
    sql+="DO $$ BEGIN IF (SELECT count(*) FROM entity_source_evidence)<>0 THEN RAISE EXCEPTION 'Unexpected backfill'; END IF; END $$;"
    sql+=f"ROLLBACK; SELECT count(*) FROM pg_namespace WHERE nspname='{schema}';"
    result=subprocess.run(['docker','exec','-i','h4u-demo-postgres','psql','-X','-U','h4u_demo_setup','-d','h4u_demo','-At','-v','ON_ERROR_STOP=1'],input=sql,text=True,capture_output=True,timeout=40)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip().splitlines()[-1]=='0'
