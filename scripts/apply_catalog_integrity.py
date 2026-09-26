"""009: mandatory transactional rehearsal, checksums, integrity and rollback checks."""
import argparse
from hashlib import sha256
from pathlib import Path
from psycopg.pq import TransactionStatus
from scripts.reconcile_sources import snapshot, audit, PROTECTED
from scripts.sync_embeddings import plan
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1] / 'db/migrations'
NAME = '009_catalog_integrity.sql'
CONSTRAINTS = {
    'entity_sources_source_entity_unique': ('entity_sources', 'UNIQUE (source_id, entity_type, entity_id)'),
    'entity_embeddings_entity_unique': ('entity_embeddings', 'UNIQUE (entity_type, entity_id)'),
}


def schema(conn):
    return (
        conn.execute("SELECT conrelid::regclass::text,conname,pg_get_constraintdef(oid),convalidated FROM pg_constraint WHERE conrelid IN ('entity_sources'::regclass,'entity_embeddings'::regclass) ORDER BY 1,2").fetchall(),
        conn.execute("SELECT pg_get_indexdef(indexrelid),indisvalid FROM pg_index WHERE indrelid IN ('entity_sources'::regclass,'entity_embeddings'::regclass) ORDER BY 1").fetchall(),
        conn.execute('SELECT version,checksum,applied_at FROM schema_migrations ORDER BY version').fetchall(),
    )


def verify(conn):
    for name, (table, definition) in CONSTRAINTS.items():
        row = conn.execute('SELECT pg_get_constraintdef(oid),convalidated FROM pg_constraint WHERE conrelid=%s::regclass AND conname=%s', (table,name)).fetchone()
        if row != (definition, True):
            raise RuntimeError('Constraint missing or unexpected: '+name)


def preflight(conn):
    audit(conn)
    with conn.cursor() as cur:
        report, _ = plan(cur)
    if any(report[k] for k in ('create','update','orphan','blocked_entities')) or report['unsupported_records']:
        raise RuntimeError('Embedding preflight failed')
    return report


def apply(conn):
    """Called inside an owned transaction; never commits independently."""
    conn.execute("SET LOCAL lock_timeout='5s'")
    conn.execute("SET LOCAL statement_timeout='30s'")
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('h4u-schema-migrations'))")
    # Stabilize catalog before ACCESS EXCLUSIVE DDL to avoid lock upgrades.
    conn.execute(sql.SQL('LOCK TABLE {} IN SHARE MODE').format(sql.SQL(',').join(
        sql.Identifier(t) for t in PROTECTED if t != 'entity_embeddings')))
    conn.execute('LOCK TABLE entity_sources,entity_embeddings IN ACCESS EXCLUSIVE MODE')
    for path in sorted(ROOT.glob('00[2-8]_*.sql')):
        row = conn.execute('SELECT checksum FROM schema_migrations WHERE version=%s',(path.name,)).fetchone()
        if row != (sha256(path.read_bytes()).hexdigest(),):
            raise RuntimeError('Prerequisite checksum mismatch: '+path.name)
    preflight(conn)
    before = snapshot(conn)
    source = (ROOT/NAME).read_bytes()
    digest = sha256(source).hexdigest()
    old = conn.execute('SELECT checksum FROM schema_migrations WHERE version=%s',(NAME,)).fetchone()
    if old:
        if old != (digest,):
            raise RuntimeError('009 checksum mismatch')
    else:
        conn.execute(source.decode())
        conn.execute('INSERT INTO schema_migrations(version,checksum) VALUES (%s,%s)',(NAME,digest))
    verify(conn)
    preflight(conn)
    if snapshot(conn) != before:
        raise RuntimeError('Data changed during DDL')
    return not bool(old)


def run(conn, persist=False):
    if not conn.autocommit or conn.info.transaction_status != TransactionStatus.IDLE:
        raise RuntimeError('Requires idle autocommit connection')
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        before = (schema(conn), snapshot(conn))
    with conn.transaction(force_rollback=True):
        apply(conn)
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        if (schema(conn),snapshot(conn)) != before:
            raise RuntimeError('Rollback mismatch or concurrent change')
    print('009 dry-run: constraints/integrity verified; schema, ledger and data rollback verified')
    if persist:
        with conn.transaction():
            changed = apply(conn)
        print('009 applied' if changed else '009 already applied; checksum and constraints verified')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    from app.db import get_connection
    with get_connection() as conn:
        conn.autocommit = True
        run(conn,args.apply)


if __name__ == '__main__':
    main()
