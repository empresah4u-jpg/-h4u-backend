"""Ephemeral credentials, signed fake events, mock HTTP, rollback-only PostgreSQL."""
import hashlib
import hmac
import json
import secrets
from datetime import datetime,timezone,timedelta
from uuid import uuid4
import httpx
import pytest
from fastapi import HTTPException
from app.messaging import service
from app.messaging.provider import Settings,MetaProvider,SendError
from app.messaging.normalization import DeliveryStatus,parse_command
from app.routers import whatsapp,reservations
from tests.test_identity_auth import identity_case,headers
from tests.test_commercial import flow,reserve,payment,request as create_request

ACCOUNT='100001'
ADDRESS='51999999999'


@pytest.fixture
def wa(identity_case,monkeypatch):
    t=identity_case; db=t['db']
    for name in ('channel_threads','inbound_messages','notification_events','message_outbox','channel_binding_events'):
        db.execute('CREATE TEMP TABLE '+name+' (LIKE public.'+name+' INCLUDING ALL) ON COMMIT DROP')
    for module in (service,whatsapp): monkeypatch.setattr(module,'get_connection',reservations.get_connection)
    secret=secrets.token_urlsafe(32); verify=secrets.token_urlsafe(32)
    monkeypatch.setenv('WHATSAPP_APP_SECRET',secret)
    monkeypatch.setenv('WHATSAPP_VERIFY_TOKEN',verify)
    monkeypatch.setenv('WHATSAPP_PHONE_NUMBER_ID',ACCOUNT)
    t.update(secret=secret,verify=verify)
    return t


def payload(mid='wamid.test',text='Hola'):
    return {'object':'whatsapp_business_account','entry':[{'changes':[{'field':'messages','value':{
        'metadata':{'phone_number_id':ACCOUNT},'messages':[{'id':mid,'from':ADDRESS,'type':'text',
            'text':{'body':text},'timestamp':str(int(datetime.now(timezone.utc).timestamp()))}]}}]}]}


def post(t,body):
    raw=json.dumps(body).encode()
    signature='sha256='+hmac.new(t['secret'].encode(),raw,hashlib.sha256).hexdigest()
    return t['client'].post('/webhooks/whatsapp',content=raw,headers={'x-hub-signature-256':signature})


def thread(t):
    assert post(t,payload()).status_code==200
    return t['db'].execute('SELECT id FROM channel_threads').fetchone()[0]


class Fake:
    def __init__(self,error=None): self.calls=[]; self.error=error
    def send_text(self,to,text,key):
        self.calls.append(key)
        if self.error: raise self.error
        return 'wamid.out.'+key
    def send_template(self,to,name,language,key): return self.send_text(to,name,key)


def queue(t,tid=None,key='key'):
    with t['db'].cursor() as cur: return service.enqueue(cur,tid or thread(t),key,'text',{'text':'Respuesta'})


def test_webhook_verification(wa):
    client=wa['client']; params={'hub.mode':'subscribe','hub.verify_token':wa['verify'],'hub.challenge':'1234'}
    response=client.get('/webhooks/whatsapp',params=params)
    assert response.status_code==200 and response.text=='1234'
    params['hub.verify_token']='wrong'
    assert client.get('/webhooks/whatsapp',params=params).status_code==403


def test_signature_size_and_unconfigured(wa,monkeypatch):
    assert wa['client'].post('/webhooks/whatsapp',json=payload()).status_code==403
    assert wa['client'].post('/webhooks/whatsapp',content=b'x'*(whatsapp.MAX_BODY+1)).status_code==413
    monkeypatch.delenv('WHATSAPP_APP_SECRET')
    assert wa['client'].post('/webhooks/whatsapp',json=payload()).status_code==503


@pytest.mark.parametrize('body',[{},[],{'object':'whatsapp_business_account','entry':None}, {'object':'whatsapp_business_account','entry':[None]}])
def test_defensive_payload(wa,body):
    response=post(wa,body)
    assert response.status_code==400 and wa['secret'] not in response.text


def test_inbound_dedup_no_identity_or_command_execution(wa):
    tid=thread(wa)
    assert post(wa,payload()).status_code==200
    assert wa['db'].execute('SELECT count(*) FROM inbound_messages').fetchone()[0]==1
    assert wa['db'].execute('SELECT traveler_id,partner_id,bound_by FROM channel_threads WHERE id=%s',(tid,)).fetchone()==(None,None,None)
    request=create_request(wa['flow']); candidate=request['candidates'][0]['request_partner_id']
    before=wa['db'].execute('SELECT status FROM request_partners WHERE id=%s',(candidate,)).fetchone()
    assert post(wa,payload('command','ACEPTAR '+str(candidate))).status_code==200
    assert wa['db'].execute('SELECT status FROM request_partners WHERE id=%s',(candidate,)).fetchone()==before
    assert parse_command('ACEPTAR '+str(candidate))['requires_authenticated_h4u']


def test_outbox_idempotency_and_fake_send(wa):
    tid=thread(wa); oid=queue(wa,tid)
    assert queue(wa,tid)==oid
    with wa['db'].cursor() as cur,pytest.raises(HTTPException): service.enqueue(cur,tid,'key','text',{'text':'different'})
    fake=Fake()
    assert service.process_one(fake,ACCOUNT)
    assert not service.process_one(fake,ACCOUNT)
    assert len(fake.calls)==1
    assert wa['db'].execute('SELECT status,attempt_count FROM message_outbox').fetchone()==('sent',1)


def test_temporary_retry_backoff_and_bound(wa):
    queue(wa); fake=Fake(SendError('rate_limited',True))
    for attempt in range(5):
        assert service.process_one(fake,ACCOUNT)
        if attempt<4:
            assert not service.process_one(fake,ACCOUNT)
            wa['db'].execute("UPDATE message_outbox SET next_attempt_at=clock_timestamp()-interval '1 second'")
    assert wa['db'].execute('SELECT status,attempt_count,last_error FROM message_outbox').fetchone()==('failed',5,'rate_limited')
    assert not service.process_one(fake,ACCOUNT)


@pytest.mark.parametrize('error',[SendError('provider_rejected'),SendError('delivery_unknown'),RuntimeError('sensitive-provider-body')])
def test_permanent_or_ambiguous_no_retry_no_leak(wa,error):
    queue(wa); fake=Fake(error)
    assert service.process_one(fake,ACCOUNT)
    assert not service.process_one(fake,ACCOUNT)
    state=wa['db'].execute('SELECT status,last_error FROM message_outbox').fetchone()
    assert state[0]=='failed' and 'sensitive' not in state[1]


def test_retry_then_success(wa):
    queue(wa); fake=Fake(SendError('connection_unavailable',True))
    service.process_one(fake,ACCOUNT)
    wa['db'].execute("UPDATE message_outbox SET next_attempt_at=clock_timestamp()-interval '1 second'")
    fake.error=None; service.process_one(fake,ACCOUNT)
    assert wa['db'].execute('SELECT status,attempt_count FROM message_outbox').fetchone()==('sent',2)


def test_delivery_order_duplicates_and_failed(wa):
    oid=queue(wa); service.process_one(Fake(),ACCOUNT)
    mid='wamid.out.'+oid
    with wa['db'].cursor() as cur:
        for state in ('delivered','sent','failed','delivered','read','sent'):
            service.apply_status(cur,DeliveryStatus(mid,ADDRESS,state,datetime.now(timezone.utc),oid),ACCOUNT)
    assert wa['db'].execute('SELECT status FROM message_outbox').fetchone()[0]=='read'


def test_signed_status_before_send_ack(wa):
    oid=queue(wa)
    class Early(Fake):
        def send_text(self,to,text,key):
            body=payload(); value=body['entry'][0]['changes'][0]['value']; value.pop('messages')
            value['statuses']=[{'id':'early','recipient_id':ADDRESS,'status':'delivered',
                'timestamp':str(int(datetime.now(timezone.utc).timestamp())),'biz_opaque_callback_data':oid}]
            assert post(wa,body).status_code==200
            return 'early'
    service.process_one(Early(),ACCOUNT)
    assert wa['db'].execute('SELECT status,provider_message_id FROM message_outbox').fetchone()==('delivered','early')


def test_stale_processing_quarantined(wa):
    queue(wa)
    wa['db'].execute("UPDATE message_outbox SET status='processing',attempt_count=1,processing_at=clock_timestamp()-interval '6 minutes'")
    fake=Fake(); assert not service.process_one(fake,ACCOUNT)
    assert not fake.calls
    assert wa['db'].execute('SELECT last_error FROM message_outbox').fetchone()[0]=='delivery_unknown'


def test_session_window_and_template_consent(wa):
    tid=thread(wa); queue(wa,tid)
    wa['db'].execute("UPDATE channel_threads SET last_inbound_at=clock_timestamp()-interval '25 hours'")
    fake=Fake(); service.process_one(fake,ACCOUNT); assert not fake.calls
    with wa['db'].cursor() as cur: service.enqueue(cur,tid,'template','template',{'name':'h4u_test','language':'es'})
    service.process_one(fake,ACCOUNT); assert not fake.calls


@pytest.mark.parametrize('role',['partner','tourist'])
def test_diagnostics_not_public(wa,role):
    user=wa['user'](role=role); h=headers(wa['token'](user))
    assert wa['client'].get('/admin/messaging/outbox',headers=h).status_code==403
    assert wa['client'].put('/admin/messaging/threads/'+str(uuid4())+'/binding',headers=h,json={'opted_in':True,'reason':'test'}).status_code==403


@pytest.mark.parametrize('role',['admin','operator'])
def test_diagnostics_safe_projection(wa,role):
    queue(wa); service.process_one(Fake(SendError('provider_rejected')),ACCOUNT)
    h=headers(wa['token'](wa['user'](role=role)))
    response=wa['client'].get('/admin/messaging/outbox?status=failed',headers=h)
    assert response.status_code==200 and response.json()['items']
    assert ADDRESS not in response.text and 'Respuesta' not in response.text and wa['secret'] not in response.text
    assert wa['client'].get('/admin/messaging/outbox?limit=101',headers=h).status_code==422


def test_admin_binding_consent_audit_and_reply(wa):
    tid=thread(wa); admin=wa['user'](role='admin'); h=headers(wa['token'](admin))
    body={'partner_id':str(wa['flow']['partner']),'opted_in':True,'reason':'Consentimiento verificado fuera del canal'}
    url='/admin/messaging/threads/'+str(tid)
    assert wa['client'].put(url+'/binding',headers=h,json=body).status_code==200
    assert wa['db'].execute('SELECT count(*) FROM channel_binding_events').fetchone()[0]==1
    response=wa['client'].post(url+'/replies',headers=h,json={'idempotency_key':'manual','text':'Respuesta'})
    assert response.status_code==202
    assert wa['client'].post(url+'/replies',headers=h,json={'idempotency_key':'manual','text':'Respuesta'}).json()==response.json()


def test_commercial_notifications_and_outage_does_not_revert(wa):
    tid=thread(wa)
    wa['db'].execute("UPDATE channel_threads SET partner_id=%s,opted_in_at=clock_timestamp(),bound_by=%s,binding_reason='fixture' WHERE id=%s",(wa['flow']['partner'],uuid4(),tid))
    res,pay=payment(wa['flow'])
    kinds={r[0] for r in wa['db'].execute('SELECT kind FROM notification_events').fetchall()}
    assert {'request.created','partner.request','partner.response','reservation.confirmed','payment.confirmed'}<=kinds
    templates={kind:{'name':'h4u_update','language':'es'} for kind in kinds}
    assert service.materialize(templates,ACCOUNT)>0
    assert service.materialize(templates,ACCOUNT)==0
    service.process_one(Fake(SendError('connection_unavailable',True)),ACCOUNT)
    assert wa['db'].execute('SELECT status FROM reservations WHERE code=%s',(res['code'],)).fetchone()[0]=='confirmed'
    assert wa['db'].execute('SELECT status FROM payments WHERE code=%s',(pay['code'],)).fetchone()[0]=='paid'


def test_cancellation_and_simulation(wa):
    res=reserve(wa['flow'])
    from tests.test_commercial import approved_cancellation
    approved_cancellation(wa['flow'],res,reason='test')
    assert wa['db'].execute("SELECT count(*) FROM notification_events WHERE kind='reservation.cancelled'").fetchone()[0]==1
    db=wa['db']; before=db.execute('SELECT count(*) FROM notification_events').fetchone()[0]
    from app.routers.service_requests import ServiceRequestCreate,create_service_request
    from datetime import date
    create_service_request(ServiceRequestCreate(session_id=wa['flow']['session'],product_id=wa['flow']['product'],service_date=date.today()+timedelta(days=7),passenger_count=1,adults_count=1,minors_count=0,simulation=True))
    assert db.execute('SELECT count(*) FROM notification_events').fetchone()[0]==before
    assert db.execute('SELECT count(*) FROM service_requests WHERE session_id=%s AND messaging_suppressed',(wa['flow']['session'],)).fetchone()[0]==1


@pytest.mark.parametrize('status,retry,code',[(429,True,'rate_limited'),(400,False,'provider_rejected'),(503,False,'delivery_unknown')])
def test_meta_http_sanitization(status,retry,code):
    settings=Settings(ACCOUNT,access_token=secrets.token_urlsafe(32),api_version='v99.0')
    client=httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(status,json={'error':'sensitive'})))
    with client,pytest.raises(SendError) as exc: MetaProvider(settings,client).send_text(ADDRESS,'Hi',str(uuid4()))
    assert exc.value.code==code and exc.value.retryable==retry and 'sensitive' not in str(exc.value)


def test_meta_payload_templates_and_timeout():
    seen=[]
    def transport(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200,json={'messages':[{'id':'out'}]})
    settings=Settings(ACCOUNT,access_token=secrets.token_urlsafe(32),api_version='v99.0')
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        adapter=MetaProvider(settings,client)
        assert adapter.send_template(ADDRESS,'approved_template','es','correlation')=='out'
        assert seen[0]['template']['language']['code']=='es'
        assert seen[0]['biz_opaque_callback_data']=='correlation'
    def timeout(req): raise httpx.ReadTimeout('sensitive',request=req)
    with httpx.Client(transport=httpx.MockTransport(timeout)) as client,pytest.raises(SendError) as exc:
        MetaProvider(settings,client).send_text(ADDRESS,'Hi','id')
    assert exc.value.code=='delivery_unknown' and not exc.value.retryable


def test_partner_cannot_execute_other_candidate_even_with_phone(wa):
    req=create_request(wa['flow']); candidate=req['candidates'][0]['request_partner_id']
    other=wa['db'].execute("INSERT INTO partners(code,business_name,status) VALUES (%s,'Fixture','active') RETURNING id",(uuid4().hex[:12],)).fetchone()[0]
    user=wa['user'](owner=other); h=headers(wa['token'](user))
    assert post(wa,payload('attempt','ACEPTAR '+str(candidate))).status_code==200
    response=wa['client'].post('/partner-responses',headers=h,json={'request_partner_id':str(candidate),'action':'accept'})
    assert response.status_code==403
    assert wa['client'].post('/partner-responses',json={'request_partner_id':str(candidate),'action':'accept'}).status_code==401


def test_wrong_account_and_future_timestamp(wa):
    body=payload(); body['entry'][0]['changes'][0]['value']['metadata']['phone_number_id']='other'
    assert post(wa,body).status_code==400
    body=payload(); body['entry'][0]['changes'][0]['value']['messages'][0]['timestamp']='99999999999'
    assert post(wa,body).status_code==400


def test_wrong_recipient_status_is_ignored(wa):
    oid=queue(wa); service.process_one(Fake(),ACCOUNT)
    with wa['db'].cursor() as cur:
        service.apply_status(cur,DeliveryStatus('wamid.out.'+oid,'51888888888','read',datetime.now(timezone.utc),oid),ACCOUNT)
    assert wa['db'].execute('SELECT status FROM message_outbox').fetchone()[0]=='sent'


def test_ambiguous_send_reconciles_from_signed_status(wa):
    oid=queue(wa); service.process_one(Fake(SendError('delivery_unknown')),ACCOUNT)
    with wa['db'].cursor() as cur:
        service.apply_status(cur,DeliveryStatus('recovered',ADDRESS,'sent',datetime.now(timezone.utc),oid),ACCOUNT)
    assert wa['db'].execute('SELECT status,provider_message_id FROM message_outbox').fetchone()==('sent','recovered')


def test_concurrent_worker_claim_does_not_send_twice(wa):
    queue(wa)
    class Reentrant(Fake):
        def send_text(self,to,text,key):
            other=Fake()
            assert not service.process_one(other,ACCOUNT)
            assert not other.calls
            return super().send_text(to,text,key)
    fake=Reentrant(); service.process_one(fake,ACCOUNT)
    assert len(fake.calls)==1


def test_notification_transaction_rollback(wa):
    db=wa['db']
    with db.transaction(force_rollback=True):
        create_request(wa['flow'])
        assert db.execute('SELECT count(*) FROM notification_events').fetchone()[0]>0
    assert db.execute('SELECT count(*) FROM notification_events').fetchone()[0]==0


def test_unconfigured_templates_remain_pending(wa):
    create_request(wa['flow'])
    assert service.materialize({},ACCOUNT)==0
    assert wa['db'].execute('SELECT count(*) FROM notification_events WHERE processed_at IS NOT NULL').fetchone()[0]==0


def test_thread_destination_admin_only_and_consent_revocation(wa):
    tid=thread(wa); url='/admin/messaging/threads/'+str(tid)
    admin=headers(wa['token'](wa['user'](role='admin')))
    operator=headers(wa['token'](wa['user'](role='operator')))
    assert wa['client'].get(url,headers=operator).status_code==403
    assert wa['client'].get(url,headers=admin).json()['address']==ADDRESS
    body={'partner_id':str(wa['flow']['partner']),'opted_in':True,'reason':'Consent verified'}
    assert wa['client'].put(url+'/binding',headers=admin,json=body).status_code==200
    with wa['db'].cursor() as cur:
        service.enqueue(cur,tid,'consent-template','template',{'name':'h4u_notice','language':'es'})
    body['opted_in']=False
    assert wa['client'].put(url+'/binding',headers=admin,json=body).status_code==200
    fake=Fake(); service.process_one(fake,ACCOUNT)
    assert not fake.calls
    assert wa['db'].execute('SELECT count(*) FROM channel_binding_events').fetchone()[0]==2


def test_binding_cannot_be_reassigned(wa):
    tid=thread(wa); h=headers(wa['token'](wa['user'](role='admin')))
    url='/admin/messaging/threads/'+str(tid)+'/binding'
    assert wa['client'].put(url,headers=h,json={'partner_id':str(wa['flow']['partner']),'opted_in':True,'reason':'verified'}).status_code==200
    traveler=wa['db'].execute('SELECT traveler_id FROM sessions WHERE id=%s',(wa['flow']['session'],)).fetchone()[0]
    assert wa['client'].put(url,headers=h,json={'traveler_id':str(traveler),'opted_in':True,'reason':'attempt'}).status_code==409


def test_worker_disabled_never_constructs_provider(monkeypatch,capsys):
    from scripts import process_whatsapp as worker
    monkeypatch.setenv('WHATSAPP_SEND_ENABLED','false')
    monkeypatch.setattr('sys.argv',['process_whatsapp'])
    def unexpected(*args,**kwargs): raise AssertionError('Provider must not be constructed')
    monkeypatch.setattr(worker,'MetaProvider',unexpected)
    worker.main()
    assert 'disabled' in capsys.readouterr().out
