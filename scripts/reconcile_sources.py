"""Apply reviewed provenance links only; default rehearses writes and rolls back."""
import argparse
import json
from pathlib import Path
from psycopg import sql
from psycopg.pq import TransactionStatus
from app.db import get_connection

TABLES = {
    'hotel': 'hotels', 'restaurant': 'restaurants', 'attraction': 'attractions',
    'tour': 'tours', 'venue': 'venues', 'emergency_service': 'emergency_services',
    'general_service': 'general_services', 'transport_provider': 'transport_providers',
    'tour_operator': 'tour_operators',
}
PROTECTED = tuple(sorted(set(TABLES.values()) | {'data_sources', 'entity_embeddings'}))
MANIFEST = Path(__file__).resolve().parents[1] / 'data/provenance/block_5a.json'


def snapshot(conn):
    """Fingerprints remain internal; no auth or commercial tables are read."""
    return {table: conn.execute(sql.SQL(
        "SELECT count(*), md5(COALESCE(string_agg(row_to_json(t)::text, '' ORDER BY id), '')) FROM {} t"
    ).format(sql.Identifier(table))).fetchone() for table in (*PROTECTED, 'entity_sources')}


def audit(conn):
    duplicates = conn.execute('''SELECT count(*) FROM (
        SELECT source_id,entity_type,entity_id FROM entity_sources
        GROUP BY 1,2,3 HAVING count(*)>1) d''').fetchone()[0]
    orphan = conn.execute('''SELECT count(*) FROM entity_sources e
        LEFT JOIN data_sources s ON s.id=e.source_id WHERE s.id IS NULL''').fetchone()[0]
    unknown = conn.execute('SELECT count(*) FROM entity_sources WHERE NOT(entity_type=ANY(%s))',
                           (list(TABLES),)).fetchone()[0]
    for kind, table in TABLES.items():
        orphan += conn.execute(sql.SQL('''SELECT count(*) FROM entity_sources e
            LEFT JOIN {} t ON t.id=e.entity_id WHERE e.entity_type=%s AND t.id IS NULL''')
            .format(sql.Identifier(table)), (kind,)).fetchone()[0]
    embedding_orphans = 0
    for kind in ('hotel', 'restaurant', 'attraction', 'tour'):
        embedding_orphans += conn.execute(sql.SQL('''SELECT count(*) FROM entity_embeddings e
            LEFT JOIN {} t ON t.id=e.entity_id WHERE e.entity_type=%s
            AND (t.id IS NULL OR t.status IS DISTINCT FROM 'active')''')
            .format(sql.Identifier(TABLES[kind])), (kind,)).fetchone()[0]
    invalid_vectors = conn.execute('''SELECT count(*) FROM entity_embeddings WHERE embedding IS NULL
        OR vector_dims(embedding)<>384 OR NOT(entity_type=ANY(%s))''',
        (['hotel','restaurant','attraction','tour'],)).fetchone()[0]
    embedding_duplicates = conn.execute('''SELECT count(*) FROM (
        SELECT entity_type,entity_id FROM entity_embeddings GROUP BY 1,2 HAVING count(*)>1) d''').fetchone()[0]
    results = dict(duplicates=duplicates, orphan=orphan, unknown_types=unknown,
                   embedding_orphans=embedding_orphans, invalid_vectors=invalid_vectors,
                   embedding_duplicates=embedding_duplicates)
    if any(results.values()):
        raise ValueError(f'Integrity check failed: {results}')
    return results


def pending(conn):
    return conn.execute('''SELECT s.id::text,s.name FROM data_sources s
        WHERE NOT EXISTS(SELECT 1 FROM entity_sources e WHERE e.source_id=s.id)
        ORDER BY s.name''').fetchall()


def insert_links(conn, manifest):
    added = []
    seen = set()
    for source in manifest['sources']:
        actual = conn.execute('SELECT name,url,status FROM data_sources WHERE id=%s',
                              (source['id'],)).fetchone()
        if actual != (source['name'], source['url'], 'active'):
            raise ValueError(f"Source drift: {source['id']}")
        for link in source['links']:
            kind = link['entity_type']
            if kind not in TABLES or not link['evidence'].strip():
                raise ValueError('Unsupported type or missing evidence')
            key = (source['id'], kind, link['entity_id'])
            if key in seen:
                raise ValueError('Duplicate manifest link')
            seen.add(key)
            actual = conn.execute(sql.SQL('SELECT code,name,status FROM {} WHERE id=%s')
                .format(sql.Identifier(TABLES[kind])), (link['entity_id'],)).fetchone()
            if actual != (link['code'], link['name'], 'active'):
                raise ValueError(f"Entity drift: {link['code']}")
            exists = conn.execute('''SELECT source_url,verification_status,notes FROM entity_sources
                WHERE source_id=%s AND entity_type=%s AND entity_id=%s''', key).fetchone()
            if exists:
                if exists != (source['url'], 'MANUAL_VERIFIED_SOURCE_MATCH', link['evidence']):
                    raise ValueError(f"Existing provenance drift: {link['code']}")
                continue
            conn.execute('''INSERT INTO entity_sources
                (source_id,entity_type,entity_id,source_url,verification_status,verified_at,notes)
                VALUES (%s,%s,%s,%s,'MANUAL_VERIFIED_SOURCE_MATCH',%s,%s)''',
                (*key, source['url'], manifest['reviewed_at'], link['evidence']))
            added.append(dict(source_id=source['id'], entity_type=kind, code=link['code']))
    return added


def run_transaction(conn, manifest, apply=False, expected=None, expected_added=None):
    with conn.transaction(force_rollback=not apply):
        conn.execute("SET LOCAL lock_timeout='5s'")
        conn.execute("SET LOCAL statement_timeout='30s'")
        # Serialize provenance writers while validating the complete reviewed plan.
        conn.execute('LOCK TABLE entity_sources IN SHARE ROW EXCLUSIVE MODE')
        conn.execute(sql.SQL('LOCK TABLE {} IN SHARE MODE').format(
            sql.SQL(',').join(map(sql.Identifier, PROTECTED))))
        before = snapshot(conn)
        if expected is not None and before != expected:
            raise ValueError('Database changed since rehearsal; rerun for a fresh review')
        audit(conn)
        added = insert_links(conn, manifest)
        if expected_added is not None and added != expected_added:
            raise ValueError('Rehearsal mismatch; transaction rolled back')
        checks = audit(conn)
        after = snapshot(conn)
        if any(before[t] != after[t] for t in PROTECTED):
            raise ValueError('Protected catalog or embeddings changed')
        if after['entity_sources'][0] != before['entity_sources'][0] + len(added):
            raise ValueError('Unexpected provenance count')
        result = dict(mode='apply' if apply else 'dry-run', added=added,
                      before={t: v[0] for t,v in before.items()},
                      after={t: v[0] for t,v in after.items()},
                      pending_sources=pending(conn), integrity=checks)
    return result, before


def reconcile(conn, manifest, apply=False):
    if not conn.autocommit or conn.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError('Requires an idle autocommit connection')
    rehearsal, baseline = run_transaction(conn, manifest)
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        if snapshot(conn) != baseline:
            raise ValueError('Rollback verification failed or concurrent data changed')
    rehearsal['rollback_verified'] = True
    if not apply:
        return rehearsal
    result, _ = run_transaction(conn, manifest, apply=True, expected=baseline,
                                expected_added=rehearsal['added'])
    result['rollback_verified'] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text())
    with get_connection() as conn:
        conn.autocommit = True
        print(json.dumps(reconcile(conn, manifest, args.apply), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
