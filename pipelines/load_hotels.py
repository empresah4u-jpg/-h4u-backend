"""Hotel preview only. No apply option, external calls, or catalog writes."""
import argparse
from collections import Counter
import json
from pathlib import Path
from psycopg.pq import TransactionStatus
from pipelines.validate_entities import HotelInput,validate_record,identity_text,normalize_url
from pipelines.provenance import classify

FIELDS=('code','name','category','address','latitude','longitude','phone','website','google_place_id','zone','status')
UNVERIFIED={None,'','WEB_DISCOVERED_NEEDS_FIELD_VALIDATION','WEB_DISCOVERED_UNVALIDATED','unverified'}


def preview(records, state):
    if not isinstance(records,list): raise ValueError('Input must be a JSON array')
    parsed=[validate_record(HotelInput,r) for r in records]
    keys=[]
    for item,_ in parsed:
        if item is None:
            keys.append([]);continue
        k=[('code',item.code),('name',item.destination,identity_text(item.name))]
        if item.google_place_id:k.append(('google',item.google_place_id))
        if item.external_id:k.append(('external',item.source_id,item.external_id))
        keys.append(k)
    counts=Counter(k for group in keys for k in group)
    output=[]
    for index,(item,errors) in enumerate(parsed):
        result={'index':index,'action':'REJECT','reasons':[], 'provenance_action':'REVIEW', 'provenance_reasons':[]}
        output.append(result)
        if errors:
            result['reasons']=errors;continue
        if any(counts[k]>1 for k in keys[index]):
            result['reasons']=['duplicate_in_batch'];continue
        destination=next((d for d in state['destinations'] if d['code']==item.destination and d['status']=='active'),None)
        source=next((s for s in state['sources'] if s['id']==item.source_id and s['status']=='active'),None)
        if destination is None or source is None:
            result['reasons']=['destination_missing_or_inactive' if destination is None else 'source_missing_or_inactive'];continue
        if item.source is not None and item.source!=source['name']:
            result['reasons']=['source_name_mismatch'];result['provenance_action']='CONFLICT';continue
        candidates=[]
        strong=[]
        for h in state['hotels']:
            exact=h['code']==item.code or (item.google_place_id and h.get('google_place_id')==item.google_place_id)
            weak=h['destination_id']==destination['id'] and identity_text(h['name'])==identity_text(item.name)
            if exact:strong.append(h)
            if exact or weak:candidates.append(h)
        result['normalized']=item.model_dump(exclude_unset=True)
        result['metadata_only']=['external_id','metadata']
        result['provenance']={'source_id':item.source_id,'entity_type':'hotel','source_url':item.source_url,'evidence':item.metadata.note,'operation':'review_required'}
        if len(candidates)>1:
            result.update(action='CONFLICT',reasons=['ambiguous_identity']);continue
        if item.destination == 'PARACAS' and item.code in {'HOT053','HOT071','HOT065'}:
            result.update(action='CONFLICT',reasons=['historical_identity_pending_review']);continue
        if not candidates:
            result['provenance_action'], reason = classify(item,source,[],[])
            result['provenance_reasons']=[reason]
            result.update(action='CREATE',reasons=['no_matching_hotel']);continue
        hotel=candidates[0]
        result['entity_id']=hotel['id']
        if not strong or hotel['destination_id']!=destination['id']:
            result.update(action='CONFLICT',reasons=['identity_or_destination_requires_review']);continue
        changes={}
        for field in FIELDS:
            if field not in item.model_fields_set:continue
            incoming=getattr(item,field);existing=hotel.get(field)
            if field=='website' and existing:
                try:existing=normalize_url(existing)
                except ValueError:pass
            if existing!=incoming:changes[field]={'existing':existing,'incoming':incoming}
        result['changes']=changes
        links=[l for l in state['links'] if l['source_id']==item.source_id and l['entity_id']==hotel['id']]
        result['provenance_action'], reason = classify(item,source,links,state.get('evidence',[]))
        result['provenance_reasons']=[reason]
        protected=hotel.get('verification_status') not in UNVERIFIED or bool(hotel.get('confidence_score')) or bool(hotel.get('last_verified_at')) or any(l.get('verification_status') for l in state['links'] if l['entity_id']==hotel['id'])
        hotel_links={l.get('id') for l in state['links'] if l['entity_id']==hotel['id']}
        protected = protected or any(e.get('verification_status') == 'verified' for e in state.get('evidence',[]) if e['entity_source_id'] in hotel_links)
        if changes and (protected or 'code' in changes or ('google_place_id' in changes and hotel.get('google_place_id'))):
            result.update(action='CONFLICT',reasons=['protected_hotel_or_identity_change'],provenance_action='CONFLICT')
            result['provenance_reasons']=['protected_hotel_or_identity_change']
        elif changes:
            result.update(action='UPDATE',reasons=['unverified_hotel_changes'])
        else:
            result.update(action='UNCHANGED',reasons=['catalog_fields_identical'])
    for result in output:
        result['catalog_action']=result['action']  # Backward-compatible alias.
        if result['action']=='CONFLICT' and result['provenance_action']=='REVIEW':
            result['provenance_reasons'].append('catalog_identity_requires_review')
        if 'provenance' in result:
            result['provenance']['operation']={'UNCHANGED':'unchanged','ADD':'add','REVIEW':'review_required','CONFLICT':'conflict'}[result['provenance_action']]
    summary={'total':len(output),**{action.lower():sum(r['action']==action for r in output) for action in ('CREATE','UPDATE','UNCHANGED','CONFLICT','REJECT')}}
    return {'mode':'preview','summary':summary,'provenance_summary':{a.lower():sum(r['provenance_action']==a for r in output) for a in ('UNCHANGED','ADD','REVIEW','CONFLICT')},'records':output}


def inspect(conn,records):
    if conn.info.transaction_status!=TransactionStatus.IDLE:
        raise ValueError('Requires an idle connection')
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        conn.execute("SET LOCAL statement_timeout='15s'")
        return preview(records,read_state(conn))


def read_state(conn):
    """Read inside the caller-owned snapshot/transaction."""
    def rows(query):return [r[0] for r in conn.execute(query).fetchall()]
    state={'destinations':rows('SELECT jsonb_build_object(\'id\',id,\'code\',code,\'status\',status) FROM destinations ORDER BY id'),
           'sources':rows('SELECT jsonb_build_object(\'id\',id,\'name\',name,\'url\',url,\'status\',status) FROM data_sources ORDER BY id'),
           'hotels':rows("SELECT to_jsonb(h) || jsonb_build_object('rating',h.rating::text,'price_observed',h.price_observed::text) FROM hotels h ORDER BY id"),
           'links':rows("SELECT to_jsonb(e) FROM entity_sources e WHERE entity_type='hotel' ORDER BY id")}
    if conn.execute("SELECT to_regclass('entity_source_evidence')").fetchone()[0] is not None:
        state['evidence']=rows("SELECT to_jsonb(e) FROM entity_source_evidence e JOIN entity_sources s ON s.id=e.entity_source_id WHERE s.entity_type='hotel' ORDER BY e.id")
    return state


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path,help='JSON array of hotel records')
    args=parser.parse_args()
    records=json.loads(args.input.read_text())
    from app.db import get_connection
    with get_connection() as conn:result=inspect(conn,records)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
