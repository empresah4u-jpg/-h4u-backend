"""Offline job processor. Transaction locks recover crashes without durable leases."""
import argparse
import json
import os
from psycopg.pq import TransactionStatus
from scripts.sync_embeddings import synchronize_hotel


def process_one(conn,*,job_id=None,model_factory=None):
    if conn.info.transaction_status!=TransactionStatus.IDLE:
        raise ValueError('Worker requires idle connection after catalog commit')
    # Never download a model or contact an external service.
    os.environ['HF_HUB_OFFLINE']='1'
    os.environ['TRANSFORMERS_OFFLINE']='1'
    with conn.transaction():
        conn.execute("SET LOCAL lock_timeout='5s'")
        conn.execute('LOCK TABLE hotels IN SHARE MODE')
        # Same order as the existing sync; protects legacy embedding writers too.
        conn.execute('LOCK TABLE entity_embeddings IN SHARE ROW EXCLUSIVE MODE')
        row=conn.execute("""SELECT id,entity_id,content_hash FROM ingestion_embedding_jobs
            WHERE status IN ('pending','failed','processing') AND (%s::uuid IS NULL OR id=%s::uuid)
            ORDER BY updated_at,id FOR UPDATE SKIP LOCKED LIMIT 1""",(job_id,job_id)).fetchone()
        if row is None:
            return dict(status='idle')
        identifier,entity_id,digest=row
        conn.execute("UPDATE ingestion_embedding_jobs SET status='processing',attempts=attempts+1,last_error=NULL,updated_at=clock_timestamp() WHERE id=%s",(identifier,))
        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    changed=synchronize_hotel(cur,entity_id,digest,model_factory=model_factory)
        except Exception as exc:
            # Never store model/DB exception text, URLs, SQL or credentials.
            error='semantic_state_or_vector_invalid' if isinstance(exc,ValueError) else 'embedding_processing_failed'
            conn.execute("UPDATE ingestion_embedding_jobs SET status='failed',last_error=%s,updated_at=clock_timestamp() WHERE id=%s",(error,identifier))
            result=dict(status='failed',job_id=str(identifier),error=error)
        else:
            conn.execute("UPDATE ingestion_embedding_jobs SET status='completed',completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE id=%s",(identifier,))
            result=dict(status='completed',job_id=str(identifier),embedding_written=changed)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true',help='Explicitly process one pending/retryable job')
    args=parser.parse_args()
    from app.db import get_admin_connection as get_connection
    with get_connection() as conn:
        if args.run:
            result=process_one(conn)
        else:
            with conn.transaction():
                conn.execute('SET TRANSACTION READ ONLY')
                result=dict(conn.execute('SELECT status,count(*) FROM ingestion_embedding_jobs GROUP BY status').fetchall())
    print(json.dumps(result))


if __name__=='__main__': main()
