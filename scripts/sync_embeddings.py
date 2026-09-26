"""Incremental canonical embedding sync. Inspection is the default; no deletions."""
import argparse
from collections import Counter
import json
import math

from psycopg.pq import TransactionStatus

from app.db import get_connection
from app.embeddings import MODEL_NAME, DIMENSIONS, get_model, vector_to_pg, check_storage
from scripts.generate_embeddings import load_entities, content_hash

ENTITY_TYPES = frozenset({'hotel', 'restaurant', 'tour', 'attraction'})


def valid_vector(value):
    """The existing generator requests finite, normalized 384-dimensional vectors."""
    if value is None:
        return False
    try:
        vector = json.loads(value) if isinstance(value, str) else value
        vector_to_pg(vector)
        return math.isclose(sum(float(x)**2 for x in vector), 1.0, abs_tol=0.001)
    except (ValueError, TypeError, OverflowError):
        return False


def unique_diagnosis(cur):
    cur.execute('''SELECT entity_type,entity_id,count(*) FROM entity_embeddings
        GROUP BY entity_type,entity_id HAVING count(*)>1 ORDER BY entity_type,entity_id''')
    duplicates = [dict(entity_type=t, entity_id=str(i), count=n) for t,i,n in cur.fetchall()]
    cur.execute('''SELECT pg_get_indexdef(i.indexrelid),i.indisunique,i.indisvalid,
        i.indpred IS NULL AND i.indexprs IS NULL,
        ARRAY(SELECT a.attname::text FROM unnest(i.indkey) WITH ORDINALITY k(attnum,ord)
              JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum
              WHERE k.ord<=i.indnkeyatts ORDER BY k.ord)
        FROM pg_index i WHERE i.indrelid='entity_embeddings'::regclass''')
    indexes = cur.fetchall()
    return dict(duplicate_groups=duplicates,
                unique_entity_key=any(unique and valid and full and
                    len(columns)==2 and set(columns)=={'entity_type','entity_id'}
                    for _,unique,valid,full,columns in indexes),
                indexes=[row[0] for row in indexes])


def plan(cur):
    check_storage(cur)
    entities = load_entities(cur)  # Exactly the existing canonical content, no second renderer.
    cur.execute('''SELECT id,entity_type,entity_id,destination_id,content,content_hash,
                          embedding_model,embedding::text FROM entity_embeddings ORDER BY entity_type,entity_id,id''')
    stored = cur.fetchall()
    by_key = {}
    for row in stored:
        by_key.setdefault((row[1],row[2]), []).append(row)
    active_keys = {(e['entity_type'],e['entity_id']) for e in entities}
    changes = []
    unchanged = 0
    blocked = 0
    reasons = Counter()
    for entity in entities:
        digest = content_hash(entity['content'])
        rows = by_key.get((entity['entity_type'],entity['entity_id']), [])
        if len(rows)>1:
            blocked += 1
            continue  # Never choose an arbitrary duplicate or silently delete history.
        row = rows[0] if rows else None
        why = []
        if row:
            if row[5] != digest: why.append('content_hash')
            if row[6] != MODEL_NAME: why.append('embedding_model')
            if not valid_vector(row[7]): why.append('vector_contract')
            if row[3] != entity['destination_id']: why.append('destination_id')
            if row[4] != entity['content']: why.append('canonical_content')
        if row and not why:
            unchanged += 1
            continue
        reasons.update(why)
        changes.append(dict(entity=entity, digest=digest, id=row[0] if row else None,
                            action='update' if row else 'create'))
    orphans = [dict(id=str(row[0]),entity_type=row[1],entity_id=str(row[2])) for row in stored
               if row[1] in ENTITY_TYPES and (row[1],row[2]) not in active_keys]
    unsupported = [dict(id=str(row[0]),entity_type=row[1],entity_id=str(row[2]))
                   for row in stored if row[1] not in ENTITY_TYPES]
    summary = dict(active_entities=len(entities),existing_embeddings=len(stored),
        create=sum(x['action']=='create' for x in changes),
        update=sum(x['action']=='update' for x in changes),unchanged=unchanged,
        orphan=len(orphans),orphan_records=orphans,unsupported_records=unsupported,
        blocked_entities=blocked,update_reasons=dict(reasons),unique=unique_diagnosis(cur))
    return summary, changes


def synchronize(conn, *, apply=False):
    """Own exactly one transaction; caller supplies an idle connection and closes it."""
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise RuntimeError('Sync requires an idle connection to own its atomic transaction')
    with conn.transaction():
        with conn.cursor() as cur:
            if not apply:
                cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            else:
                cur.execute("SET LOCAL lock_timeout='5s'")
                # Also excludes legacy writers that do not use an advisory lock/UNIQUE.
                # Sources cannot change while their content is encoded and persisted.
                cur.execute('LOCK TABLE hotels,restaurants,tours,attractions IN SHARE MODE')
                cur.execute('LOCK TABLE entity_embeddings IN SHARE ROW EXCLUSIVE MODE')
            summary, changes = plan(cur)
            summary['mode'] = 'apply' if apply else 'inspection'
            summary['written'] = 0
            if not apply:
                return summary
            if summary['unique']['duplicate_groups']:
                raise RuntimeError('Duplicate embedding keys require review; nothing applied')
            if not changes:
                return summary  # Do not load the model or touch any timestamp.
            model = get_model()
            for change in changes:
                entity = change['entity']
                vector = model.encode(entity['content'], normalize_embeddings=True)
                pg_vector = vector_to_pg(vector)
                if not valid_vector(vector):
                    raise ValueError('Generated embedding violates normalized vector(384) contract')
                values = (entity['destination_id'],entity['entity_type'],entity['entity_id'],
                          entity['content'],pg_vector,MODEL_NAME,change['digest'])
                if change['action']=='create':
                    cur.execute('''INSERT INTO entity_embeddings(destination_id,entity_type,entity_id,
                        content,embedding,embedding_model,content_hash) VALUES (%s,%s,%s,%s,%s::vector,%s,%s)''', values)
                else:
                    cur.execute('''UPDATE entity_embeddings SET destination_id=%s,entity_type=%s,entity_id=%s,
                        content=%s,embedding=%s::vector,embedding_model=%s,content_hash=%s,
                        updated_at=clock_timestamp() WHERE id=%s''', (*values,change['id']))
                if cur.rowcount != 1:
                    raise RuntimeError('Unexpected number of embedding rows affected')
                summary['written'] += 1
            return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Explicitly write only CREATE/UPDATE in one transaction')
    args = parser.parse_args()
    with get_connection() as conn:
        result = synchronize(conn, apply=args.apply)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
