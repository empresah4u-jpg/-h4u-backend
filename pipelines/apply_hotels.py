"""Apply only validated plans; default CLI operation is strictly READ ONLY."""
import argparse
import json
from pathlib import Path
from psycopg import sql
from psycopg.types.json import Jsonb
from psycopg.pq import TransactionStatus
from pipelines.ingestion_plan import validate, StalePlan, InvalidPlan
from pipelines.load_hotels import read_state

LOCKS='LOCK TABLE hotels,destinations,data_sources,entity_sources IN SHARE ROW EXCLUSIVE MODE'


def apply(conn,plan,*,dry_run=True):
    validate(plan)
    if conn.info.transaction_status!=TransactionStatus.IDLE:
        raise InvalidPlan('Apply requires idle connection')
    with conn.transaction():
        if dry_run:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        else:
            conn.execute("SET LOCAL lock_timeout='5s'")
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('h4u-hotel-ingestion'))")
            conn.execute(LOCKS)
            conn.execute("SELECT lock_ingestion_source_evidence()")
        existing=conn.execute('SELECT id::text,plan_fingerprint,status,result FROM ingestion_plans WHERE id=%s OR plan_fingerprint=%s ORDER BY id',
                              (plan['plan_id'],plan['plan_fingerprint'])).fetchall()
        for identifier,digest,status,result in existing:
            if identifier==plan['plan_id'] and digest!=plan['plan_fingerprint']:
                raise InvalidPlan('Plan ID reused with different content')
        if existing:
            if any(row[2]!='applied' for row in existing):
                raise InvalidPlan('Unfinished ingestion ledger')
            return dict(mode='dry-run' if dry_run else 'apply',idempotent=True,result=existing[0][3])
        if read_state(conn)!=plan['expected_state']:
            raise StalePlan('STALE: preview snapshot changed')
        summary=dict(plan_id=plan['plan_id'],records=len(plan['entries']),
                     embedding_jobs=sum(e['embedding_required'] for e in plan['entries']))
        if dry_run:
            return dict(mode='dry-run',idempotent=False,result=summary,operations=plan['entries'])
        conn.execute("INSERT INTO ingestion_plans(id,contract_version,plan_fingerprint,entity_type,plan,status) VALUES (%s,1,%s,'hotel',%s,'applying')",
                     (plan['plan_id'],plan['plan_fingerprint'],Jsonb(plan)))
        applied=[]
        for entry in plan['entries']:
            identifier=entry['entity_id']
            values=entry['new_values']
            if entry['catalog_action']=='CREATE':
                values=dict(values,id=identifier,destination_id=entry['destination_id'])
                identifier=str(conn.execute(sql.SQL('INSERT INTO hotels ({}) VALUES ({}) RETURNING id').format(
                    sql.SQL(',').join(map(sql.Identifier,values)),sql.SQL(',').join(sql.Placeholder() for _ in values)),tuple(values.values())).fetchone()[0])
            elif entry['catalog_action']=='UPDATE':
                # Fields are whitelisted and rederived by validated-plan validation.
                conn.execute(sql.SQL('UPDATE hotels SET {},updated_at=clock_timestamp() WHERE id=%s').format(
                    sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(k)) for k in values)),(*values.values(),identifier))
            if entry['provenance_action']=='ADD':
                link=conn.execute("SELECT id FROM entity_sources WHERE source_id=%s AND entity_type='hotel' AND entity_id=%s",(entry['source_id'],identifier)).fetchone()
                if not link:
                    link=conn.execute("INSERT INTO entity_sources(source_id,entity_type,entity_id,source_url) VALUES (%s,'hotel',%s,%s) RETURNING id",(entry['source_id'],identifier,entry['source_url'])).fetchone()
                ev=entry['evidence'];metadata={k:v for k,v in ev.items() if k not in ('note','source_reference','observed_at') and v is not None}
                args=(link[0],entry['source_url'],ev.get('source_reference'),ev['note'],ev.get('observed_at'),Jsonb(metadata))
                duplicate=conn.execute('''SELECT id FROM entity_source_evidence WHERE entity_source_id=%s
                    AND source_url IS NOT DISTINCT FROM %s AND source_reference IS NOT DISTINCT FROM %s
                    AND notes=%s AND observed_at IS NOT DISTINCT FROM %s::timestamptz AND metadata=%s''',args).fetchone()
                if not duplicate:
                    conn.execute('''INSERT INTO entity_source_evidence(entity_source_id,source_url,source_reference,notes,observed_at,metadata)
                        VALUES (%s,%s,%s,%s,%s,%s)''',args)
            if entry['embedding_required']:
                conn.execute("INSERT INTO ingestion_embedding_jobs(ingestion_plan_id,entity_type,entity_id,content_hash) VALUES (%s,'hotel',%s,%s)",
                             (plan['plan_id'],identifier,entry['semantic_hash']))
            applied.append(dict(entity_id=identifier,catalog_action=entry['catalog_action'],provenance_action=entry['provenance_action']))
        summary['entities']=applied
        conn.execute("UPDATE ingestion_plans SET status='applied',result=%s,applied_at=clock_timestamp() WHERE id=%s",(Jsonb(summary),plan['plan_id']))
    return dict(mode='apply',idempotent=False,result=summary)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan',type=Path)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run',action='store_true')
    mode.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    from app.db import get_connection
    with get_connection() as conn:
        result=apply(conn,json.loads(args.plan.read_text()),dry_run=not args.apply)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
