"""Verify every vector numerically before relabeling; never rewrites vectors."""
import argparse
import json
import numpy as np
from app.db import get_connection
from app.embeddings import MODEL_NAME, get_model, check_storage


def repair(apply=False):
    with get_connection() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        with conn.cursor() as cur:
            check_storage(cur)
            cur.execute('SELECT id,content,embedding::text,embedding_model FROM entity_embeddings WHERE embedding IS NOT NULL ORDER BY id')
            rows=cur.fetchall()
    if not rows:
        print('No vectors to verify')
        return
    expected=get_model().encode([r[1] for r in rows],normalize_embeddings=True,show_progress_bar=False)
    actual=np.asarray([json.loads(r[2]) for r in rows],dtype=np.float32)
    errors=np.max(np.abs(expected-actual),axis=1)
    if not np.all(errors < 0.0001):
        raise RuntimeError('Some vectors do not match the configured model; no metadata was changed')
    print(f'Verified {len(rows)} vectors; max error={float(errors.max())}; target={MODEL_NAME}')
    if not apply:
        print('Read-only. Use --apply to correct only proven metadata.')
        return
    changed=0
    with get_connection() as conn:
        for row in rows:
            if row[3]==MODEL_NAME:
                continue
            result=conn.execute('''UPDATE entity_embeddings SET embedding_model=%s
                WHERE id=%s AND content=%s AND embedding::text=%s
                  AND embedding_model IS NOT DISTINCT FROM %s RETURNING id''',
                (MODEL_NAME,row[0],row[1],row[2],row[3])).fetchone()
            if not result:
                raise RuntimeError('Concurrent embedding change: metadata correction rolled back')
            changed+=1
    print(f'Metadata corrected: {changed}; vectors rewritten: 0')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    repair(apply=parser.parse_args().apply)
