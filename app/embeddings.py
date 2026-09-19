"""Single embedding contract shared by search, generation and rebuilding."""
from functools import lru_cache
from threading import Lock
import math

MODEL_NAME = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
DIMENSIONS = 384
_model_lock = Lock()


@lru_cache(maxsize=1)
def get_model():
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(MODEL_NAME)
    if model.get_sentence_embedding_dimension() != DIMENSIONS:
        raise RuntimeError('Embedding model dimension does not match the storage contract')
    return model


def encode_query(query, **kwargs):
    with _model_lock:
        return get_model().encode(query, **kwargs)


def vector_to_pg(vector):
    if len(vector) != DIMENSIONS or not all(math.isfinite(float(x)) for x in vector):
        raise ValueError('Embedding must contain 384 finite values')
    return '[' + ','.join(str(float(x)) for x in vector) + ']'


def check_storage(cur):
    cur.execute("SELECT format_type(atttypid,atttypmod) FROM pg_attribute WHERE attrelid='entity_embeddings'::regclass AND attname='embedding'")
    if cur.fetchone() != ('vector(384)',):
        raise RuntimeError('Expected entity_embeddings.embedding vector(384)')
