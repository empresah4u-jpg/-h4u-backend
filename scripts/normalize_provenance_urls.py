"""Normalize only individually reviewed URLs; mandatory rollback rehearsal."""
import argparse
import json
from pathlib import Path
from psycopg import sql
from psycopg.pq import TransactionStatus
from scripts.reconcile_sources import TABLES, PROTECTED, snapshot, audit

MANIFEST = Path(__file__).resolve().parents[1]/'data/provenance/url_normalization.json'


def transaction(conn, reviewed, persist=False, expected=None):
    with conn.transaction(force_rollback=not persist):
        conn.execute("SET LOCAL lock_timeout='5s'")
        conn.execute("SET LOCAL statement_timeout='30s'")
        conn.execute('LOCK TABLE entity_sources IN SHARE ROW EXCLUSIVE MODE')
        conn.execute(sql.SQL('LOCK TABLE {} IN SHARE MODE').format(sql.SQL(',').join(map(sql.Identifier,PROTECTED))))
        before = snapshot(conn)
        if expected is not None and before != expected:
            raise ValueError('Database changed since rehearsal')
        audit(conn)
        rows = {str(i): row for i,row in conn.execute('SELECT id,to_jsonb(e) FROM entity_sources e').fetchall()}
        changed = []
        for item in reviewed:
            row = rows.get(item['id'])
            if row is None or any(row[k] != item[k] for k in ('source_id','entity_type','entity_id')):
                raise ValueError('Reviewed relation drift')
            if not item['evidence'].strip():
                raise ValueError('Missing evidence')
            source = conn.execute('SELECT name,url FROM data_sources WHERE id=%s',(item['source_id'],)).fetchone()
            if source != (item['source_name'],item['url']):
                raise ValueError('Source drift')
            actual = conn.execute(sql.SQL('SELECT code,name FROM {} WHERE id=%s').format(
                sql.Identifier(TABLES[item['entity_type']])),(item['entity_id'],)).fetchone()
            if actual != (item['code'],item['name']):
                raise ValueError('Entity drift')
            if row['source_url'] == item['url']:
                continue
            if row['source_url'] is not None:
                raise ValueError('Refusing to replace an existing URL')
            conn.execute('UPDATE entity_sources SET source_url=%s WHERE id=%s AND source_url IS NULL',(item['url'],item['id']))
            row['source_url'] = item['url']
            changed.append(item['id'])
        after = snapshot(conn)
        if any(before[t] != after[t] for t in PROTECTED):
            raise ValueError('Protected data changed')
        actual = {str(i): row for i,row in conn.execute('SELECT id,to_jsonb(e) FROM entity_sources e').fetchall()}
        if actual != rows:
            raise ValueError('Unexpected provenance changes')
        audit(conn)
    return changed,before


def normalize(conn,reviewed,persist=False):
    if not conn.autocommit or conn.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError('Requires idle autocommit connection')
    changed,before = transaction(conn,reviewed)
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        if snapshot(conn) != before:
            raise ValueError('Rollback verification failed or concurrent change')
    if persist:
        changed,_ = transaction(conn,reviewed,True,before)
    return dict(mode='apply' if persist else 'dry-run',changed=changed,rollback_verified=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    from app.db import get_connection
    with get_connection() as conn:
        conn.autocommit=True
        print(json.dumps(normalize(conn,json.loads(MANIFEST.read_text()),args.apply),indent=2))


if __name__=='__main__':
    main()
