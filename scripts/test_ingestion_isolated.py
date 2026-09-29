"""Run pytest on a disposable, fictitious DB in the isolated demo container.

No connections to production. No users/sessions/catalog rows are copied.
"""
import os
from pathlib import Path
import secrets
import subprocess
import sys
from uuid import uuid4
from scripts.setup_demo import command, docker, sql, CONTAINER, ADMIN


def main():
    if command(['docker','inspect','-f','{{index .Config.Labels "com.h4u.demo"}}',CONTAINER]).strip()!='isolated':
        raise RuntimeError('Not an isolated container')
    token=uuid4().hex
    database='h4u_ingestion_test_'+token
    role='h4u_test_'+token
    password=secrets.token_urlsafe(40)
    runtime='h4u_test_runtime_'+token
    runtime_password=secrets.token_urlsafe(40)
    created_runtime=False
    created_role=created_db=False
    try:
        sql('postgres',f"CREATE ROLE {role} LOGIN PASSWORD '{password}';")
        created_role=True
        sql('postgres',f"CREATE ROLE {runtime} NOINHERIT LOGIN PASSWORD '{runtime_password}';")
        created_runtime=True
        docker(['createdb','-U',ADMIN,'-O',role,database]);created_db=True
        sql('postgres',f'REVOKE ALL ON DATABASE {database} FROM PUBLIC;')
        schema=docker(['pg_dump','-U',ADMIN,'-d','h4u_demo','--schema-only','--no-owner','--no-privileges','--exclude-table=public.ingestion_plans','--exclude-table=public.ingestion_embedding_jobs'])
        sql(database,schema)
        # Keep the baseline at 010 even after persistent demo deploys 011.
        # These are installer-created objects in this new, empty disposable DB only.
        sql(database,'DROP FUNCTION IF EXISTS public.ingestion_plan_guard(), public.ingestion_plan_committed(), public.ingestion_job_guard(), public.lock_ingestion_source_evidence();')
        sql(database,f'GRANT ALL ON SCHEMA public TO {role}; GRANT ALL ON ALL TABLES IN SCHEMA public TO {role} WITH GRANT OPTION; GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO {role};')
        from hashlib import sha256
        ledger=''.join("INSERT INTO schema_migrations(version,checksum) VALUES ('"+p.name+"','"+sha256(p.read_bytes()).hexdigest()+"');" for p in sorted(Path('db/migrations').glob('*.sql')) if '002_'<=p.name<='010_entity_source_evidence.sql')
        sql(database,ledger)
        env=os.environ.copy()
        env.update(H4U_TEST_RUNTIME=runtime,H4U_TEST_RUNTIME_PASSWORD=runtime_password,DB_HOST='127.0.0.1',DB_PORT='55432',DB_NAME=database,DB_USER=role,DB_PASSWORD=password,
                   H4U_DEMO_MODE='false',PYTHON_DOTENV_DISABLED='true',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
                   JWT_SECRET=secrets.token_urlsafe(48),JWT_ALGORITHM='HS256',JWT_EXPIRE_MINUTES='60',WHATSAPP_SEND_ENABLED='false')
        # Only synthetic catalog rows; valid normalized vectors, no model download.
        import psycopg
        from app.embeddings import MODEL_NAME,vector_to_pg
        from scripts.generate_embeddings import load_entities,content_hash
        with psycopg.connect(host='127.0.0.1',port=55432,dbname=database,user=role,password=password) as conn:
            dest=conn.execute("INSERT INTO destinations(code,name,slug) VALUES ('FICTITIOUS','Fictional destination','fictitious') RETURNING id").fetchone()[0]
            for table in ('hotels','restaurants','tours','attractions'):
                conn.execute(f"INSERT INTO {table}(destination_id,code,name,status) VALUES (%s,'FICTITIOUS','Fictional entity','active')",(dest,))
            with conn.cursor() as cur:
                for entity in load_entities(cur):
                    cur.execute('INSERT INTO entity_embeddings(destination_id,entity_type,entity_id,content,embedding,embedding_model,content_hash) VALUES (%s,%s,%s,%s,%s::vector,%s,%s)',
                        (dest,entity['entity_type'],entity['entity_id'],entity['content'],vector_to_pg([1.0]+[0.0]*383),MODEL_NAME,content_hash(entity['content'])))
        result=subprocess.run([sys.executable,'-m','pytest',*(sys.argv[1:] or ['-q'])],env=env)
        return result.returncode
    finally:
        # Only identifiers generated above, only on the isolated container.
        if created_db:
            docker(['dropdb','-U',ADMIN,'--force',database])
        if created_runtime:
            sql('postgres',f'DROP ROLE {runtime};')
        if created_role:
            sql('postgres',f'DROP ROLE {role};')


if __name__=='__main__': raise SystemExit(main())
