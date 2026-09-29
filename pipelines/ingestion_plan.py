"""Canonical validated hotel plans. Hashes provide integrity, not authorization."""
from copy import deepcopy
from hashlib import sha256
import json
from uuid import UUID, uuid5, NAMESPACE_URL
from pipelines.load_hotels import preview, read_state, FIELDS
from scripts.generate_embeddings import hotel_content, content_hash


class InvalidPlan(ValueError):
    pass


class StalePlan(ValueError):
    pass


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)


def fingerprint(value):
    return sha256(canonical(value).encode()).hexdigest()


def build(records, state, plan_id=None):
    report=preview(records,state)
    entries=[]
    for row in report['records']:
        if row['catalog_action'] not in ('CREATE','UPDATE','UNCHANGED') or row['provenance_action'] not in ('ADD','UNCHANGED'):
            raise InvalidPlan('Non-automatic preview action')
        normalized=row['normalized']
        old=next((h for h in state['hotels'] if h['id']==row.get('entity_id')),None)
        values={k:v for k,v in normalized.items() if k in FIELDS}
        if old is None:
            values.setdefault('status','active')
        after={**(old or {}),**values}
        if after.get('status','active')!='active':
            raise InvalidPlan('Inactive hotels require manual embedding retirement review')
        destination=next(d for d in state['destinations'] if d['code']==normalized['destination'])
        after['destination_id']=destination['id']
        semantic=content_hash(hotel_content(after))
        changed=row['catalog_action']!='UNCHANGED' and (old is None or old.get('status')!='active' or content_hash(hotel_content(old))!=semantic)
        target_id=row.get('entity_id') or str(uuid5(NAMESPACE_URL,'h4u-hotel:'+destination['id']+':'+values['code']))
        entries.append(dict(entity_id=target_id,catalog_action=row['catalog_action'],
            provenance_action=row['provenance_action'],expected_state=old,new_values=values,
            destination_id=destination['id'],source_id=normalized['source_id'],
            source_url=normalized['source_url'],evidence=normalized['metadata'],
            semantic_hash=semantic,embedding_required=changed))
    if not entries:
        raise InvalidPlan('Empty ingestion batch')
    body=dict(contract_version=1,entity_type='hotel',records=deepcopy(records),
              expected_state=deepcopy(state),entries=entries)
    digest=fingerprint(body)
    return dict(body,plan_id=str(UUID(plan_id)) if plan_id else str(uuid5(NAMESPACE_URL,'h4u-ingestion:'+digest)),plan_fingerprint=digest)


def validate(plan):
    try:
        if set(plan)!= {'contract_version','entity_type','records','expected_state','entries','plan_id','plan_fingerprint'}:
            raise InvalidPlan('Invalid validated-plan fields')
        if canonical(plan) != canonical(build(plan['records'],plan['expected_state'],plan['plan_id'])):
            raise InvalidPlan('Plan fingerprint or preview classification mismatch')
    except (KeyError,TypeError,ValueError,StopIteration) as exc:
        raise InvalidPlan('Invalid or non-automatic validated plan') from exc
    return plan


def create(conn,records,plan_id=None):
    from psycopg.pq import TransactionStatus
    if conn.info.transaction_status!=TransactionStatus.IDLE:
        raise InvalidPlan('Plan requires idle connection')
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        return build(records,read_state(conn),plan_id)
