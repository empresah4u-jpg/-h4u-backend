import pytest
from app.db import get_connection
from app.embeddings import vector_to_pg, check_storage, MODEL_NAME


@pytest.mark.parametrize('vector', [[0.0]*383,[0.0]*385,[float('nan')]*384,[float('inf')]*384])
def test_invalid_embedding_rejected_before_sql(vector):
    with pytest.raises(ValueError):
        vector_to_pg(vector)


def test_existing_vectors_match_configured_storage_and_metadata():
    with get_connection() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        with conn.cursor() as cur:
            check_storage(cur)
            cur.execute('SELECT count(*) FROM entity_embeddings WHERE embedding IS NOT NULL AND (embedding_model IS DISTINCT FROM %s OR vector_dims(embedding)<>384)',(MODEL_NAME,))
            assert cur.fetchone()[0]==0
