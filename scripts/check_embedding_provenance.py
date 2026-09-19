"""Read-only numerical provenance check. Prints counts/errors, never content."""
import json
import numpy as np
from app.db import get_connection

MODELS = (
    'sentence-transformers/all-MiniLM-L6-v2',
    'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2',
)


def main():
    with get_connection() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        dimension = conn.execute("SELECT format_type(atttypid,atttypmod) FROM pg_attribute WHERE attrelid='entity_embeddings'::regclass AND attname='embedding'").fetchone()[0]
        rows = conn.execute('SELECT embedding_model,content,embedding::text FROM entity_embeddings WHERE embedding IS NOT NULL ORDER BY id').fetchall()
    print('column_type:', dimension, 'rows:', len(rows))
    if not rows:
        return
    vectors = np.asarray([json.loads(row[2]) for row in rows],dtype=np.float32)
    from sentence_transformers import SentenceTransformer
    for name in MODELS:
        model = SentenceTransformer(name)
        if model.get_sentence_embedding_dimension() != vectors.shape[1]:
            print(name, 'incompatible dimension')
            continue
        actual = model.encode([r[1] for r in rows],normalize_embeddings=True,show_progress_bar=False)
        errors = np.max(np.abs(actual-vectors),axis=1)
        print(json.dumps({'model':name,'dimension':actual.shape[1], 'matching_vectors':int(np.sum(errors<0.0001)), 'max_absolute_error':float(np.max(errors)), 'declared_matches':sum(r[0]==name for r in rows)}))


if __name__=='__main__':
    main()
