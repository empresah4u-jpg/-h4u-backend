from copy import deepcopy
import pytest
from pipelines.load_hotels import preview,inspect
from pipelines.validate_entities import normalize_url

SOURCE='11111111-1111-4111-8111-111111111111'
DEST='22222222-2222-4222-8222-222222222222'
HOTEL='33333333-3333-4333-8333-333333333333'


def record(**changes):
    return dict(source_id=SOURCE,source_url='https://example.invalid/',destination='FICTITIOUS',
                code='FAKE001',name='Fictional Hotel',metadata={'note':'Synthetic evidence'},**changes)


def state(existing=False,verified=False):
    return {'destinations':[dict(id=DEST,code='FICTITIOUS',status='active')],
            'sources':[dict(id=SOURCE,name='Fictional source',url='https://example.invalid/',status='active')],
            'hotels':[dict(id=HOTEL,destination_id=DEST,code='FAKE001',name='Fictional Hotel',phone='123',verification_status='google_places_verified' if verified else 'unverified')] if existing else [],'links':[]}


def action(data,catalog):return preview([data],catalog)['records'][0]['action']


def test_create():assert action(record(),state())=='CREATE'
def test_unchanged():assert action(record(),state(True))=='UNCHANGED'
def test_update():assert action(record(phone='456'),state(True))=='UPDATE'
def test_protected():assert action(record(phone='456'),state(True,True))=='CONFLICT'


@pytest.mark.parametrize('field,value',[('name',''),('latitude',91),('latitude',True),('website','javascript:x'),('website','https://user:pass@example.invalid'),('phone',123),('status','invented'),('source_id','bad'),('destination','missing'),('category',None)])
def test_invalid(field,value):
    data=record();data[field]=value
    assert action(data,state())=='REJECT'


def test_missing_required():
    data=record();del data['name']
    assert action(data,state())=='REJECT'


def test_duplicates_reject_all_occurrences():
    result=preview([record(),record()],state())
    assert result['summary']['reject']==2


def test_url_normalization_and_idempotency():
    data=record(website=' HTTPS://Example.INVALID:443 ')
    catalog=state(True);catalog['hotels'][0]['website']='https://example.invalid/'
    before=deepcopy((data,catalog))
    result=preview([data],catalog)
    assert result==preview([data],catalog)
    assert result['summary']['unchanged']==1
    assert (data,catalog)==before
    assert normalize_url('https://example.invalid/a?x=1#part')=='https://example.invalid/a?x=1#part'


def test_ambiguous_name_never_creates_duplicate():
    data=record();data['code']='NEW'
    assert action(data,state(True))=='CONFLICT'


def test_destination_conflict():
    catalog=state(True);catalog['hotels'][0]['destination_id']='other'
    assert action(record(),catalog)=='CONFLICT'


def test_provenance_null_preserved_for_review():
    catalog=state(True)
    catalog['links']=[dict(source_id=SOURCE,entity_id=HOTEL,source_url=None)]
    result=preview([record()],catalog)['records'][0]
    assert result['catalog_action']=='UNCHANGED' and result['provenance_action']=='REVIEW'
    assert catalog['links'][0]['source_url'] is None


def test_metadata_does_not_become_catalog_fields():
    data=record();data['metadata'].update(description='Fictitious description',stars=4)
    result=preview([data],state())['records'][0]
    assert result['action']=='CREATE' and result['metadata_only']==['external_id','metadata']


def test_preview_postgres_is_read_only(monkeypatch):
    from app.db import get_connection
    from pipelines import load_hotels
    original=load_hotels.preview
    with get_connection() as conn:
        def checked(records,catalog):
            assert conn.execute('SHOW transaction_read_only').fetchone()==('on',)
            return original(records,catalog)
        monkeypatch.setattr(load_hotels,'preview',checked)
        assert inspect(conn,[])['summary']['total']==0


def test_confidence_alone_protects_hotel():
    catalog=state(True);catalog['hotels'][0]['confidence_score']=0.95
    assert action(record(phone='456'),catalog)=='CONFLICT'


def test_existing_evidence_cannot_be_replaced():
    catalog=state(True)
    catalog['links']=[dict(source_id=SOURCE,entity_id=HOTEL,source_url='https://example.invalid/',notes='Earlier reviewed evidence')]
    before=deepcopy(catalog)
    result=preview([record()],catalog)['records'][0]
    assert result['catalog_action']=='UNCHANGED' and result['provenance_action']=='ADD'
    assert catalog==before


def test_valid_coordinates_and_phone():
    assert action(record(latitude=-12.0,longitude=-77.0,phone='+51 (999) 123-456'),state())=='CREATE'
    assert action(record(phone='not a phone'),state())=='REJECT'


def test_unknown_fields_are_rejected():
    data=record();data['verification_status']='verified'
    assert action(data,state())=='REJECT'


def test_mixed_fictitious_preview():
    catalog=state(True)
    rows=[]
    for code,name in [('NEW','New fictional hotel'),('SAME','Same fictional hotel'),('CHANGE','Changed fictional hotel'),('SAFE','Protected fictional hotel')]:
        data=record();data.update(code=code,name=name)
        rows.append(data)
        if code!='NEW':
            catalog['hotels'].append(dict(id=code,destination_id=DEST,code=code,name=name,phone='123',verification_status='verified' if code=='SAFE' else 'unverified'))
    rows[2]['phone']='456';rows[3]['phone']='456'
    rows.append({'name':'Invalid fictional record'})
    result=preview(rows,catalog)
    assert result['summary']==dict(total=5,create=1,update=1,unchanged=1,conflict=1,reject=1)
    assert preview(rows,catalog)==result
