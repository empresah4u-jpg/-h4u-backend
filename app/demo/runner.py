"""Run real authenticated H4U endpoints against the dedicated DEMO database."""
from datetime import date,timedelta,datetime,timezone
from decimal import Decimal
import secrets
from uuid import UUID,uuid4
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from app.db import get_connection
from app.demo.safety import verify
from app.demo.provider import FakeProvider
from app.main import app
from app.messaging import service as messaging
from app.messaging.normalization import DeliveryStatus
from app.services.passwords import hash_password


class DemoFailure(RuntimeError):
    pass


def run(scenario='accept'):
    if scenario not in {'accept','counter_offer'}: raise ValueError('Unsupported demo scenario')
    run_id=uuid4(); suffix=run_id.hex[:12]
    code='DEMO-'+suffix
    account='000'+str(run_id.int)[:20]
    credentials={role:(role+'.'+suffix+'@example.invalid',secrets.token_urlsafe(32)) for role in ('admin','tourist','partner')}
    summary={'run_id':str(run_id),'environment':'DEMO','simulated':True,'scenario':scenario,'steps':[]}
    with get_connection() as conn:
        verify(conn)
        conn.execute('INSERT INTO demo_runs(id,scenario) VALUES (%s,%s)',(run_id,scenario))
    tokens={}
    try:
        with get_connection() as conn:
            verify(conn)
            destination=conn.execute("INSERT INTO destinations(code,name,slug) VALUES (%s,'Destino DEMO',%s) RETURNING id",(code,code.lower())).fetchone()[0]
            traveler=conn.execute('INSERT INTO travelers DEFAULT VALUES RETURNING id').fetchone()[0]
            session=conn.execute("INSERT INTO sessions(traveler_id,destination_id,channel) VALUES (%s,%s,'demo') RETURNING id",(traveler,destination)).fetchone()[0]
            product=conn.execute("""INSERT INTO products(destination_id,code,name,slug,product_type,status,reservations_enabled,requires_payment)
                VALUES (%s,%s,'Tour DEMO',%s,'tour','active',true,true) RETURNING id""",(destination,code,code.lower())).fetchone()[0]
            for role in ('admin','tourist'):
                email,password=credentials[role]
                conn.execute('''INSERT INTO users(email,password_hash,role,status,traveler_id)
                    VALUES (%s,%s,%s,'active',%s)''',(email,hash_password(password),role,traveler if role=='tourist' else None))
        with TestClient(app,raise_server_exceptions=False) as client:
            def call(method,path,role=None,body=None,expected=200):
                response=client.request(method,path,json=body,headers={'Authorization':'Bearer '+tokens[role]} if role else {})
                if response.status_code!=expected:
                    # Never include request/response bodies, passwords or access tokens in errors.
                    raise DemoFailure('Demo step failed: '+method+' '+path+' status='+str(response.status_code))
                return response.json()
            def login(role):
                email,password=credentials[role]
                tokens[role]=call('POST','/auth/login',body={'email':email,'password':password})['access_token']
            def record(step,**data):
                summary['steps'].append({'step':step,**data})
                with get_connection() as conn:
                    conn.execute('UPDATE demo_runs SET summary=%s WHERE id=%s',(Jsonb(summary),run_id))
            login('admin'); login('tourist')
            try:
                partner=call('POST','/admin/partners','admin',{'code':code,'business_name':'Partner DEMO '+suffix,'reason':'Provision DEMO aislada'},201)
                email,password=credentials['partner']
                call('POST','/admin/users','admin',{'email':email,'password':password,'role':'partner','partner_id':partner['id'],'membership_role':'owner','reason':'Owner ficticio DEMO'},201)
                call('PATCH','/admin/partners/'+partner['id']+'/status','admin',{'status':'active','reservations_enabled':True,'reason':'Activacion DEMO'})
                login('partner')
                # Fixture finance config only, protected by DB identity guard, not a new public capability.
                with get_connection() as conn:
                    verify(conn)
                    conn.execute("UPDATE partners SET commission_type='percentage',commission_value=10 WHERE id=%s",(partner['id'],))
                    conn.execute('INSERT INTO product_partners(product_id,partner_id,partner_price,currency) VALUES (%s,%s,100,\'PEN\')',(product,partner['id']))
                    threads=[]
                    for index in range(2):
                        threads.append(conn.execute('''INSERT INTO channel_threads(account_id,address,last_inbound_at)
                            VALUES (%s,%s,clock_timestamp()) RETURNING id''',(account,'00000000000'+str(index))).fetchone()[0])
                for tid,target in zip(threads,({'traveler_id':str(traveler)},{'partner_id':partner['id']})):
                    call('PUT','/admin/messaging/threads/'+str(tid)+'/binding','admin',{**target,'opted_in':True,'reason':'Consentimiento ficticio exclusivo DEMO'})
                record('actors',traveler_id=str(traveler),partner_id=partner['id'])
                req=call('POST','/service-requests','tourist',{'session_id':str(session),'product_id':str(product),
                    'service_date':str(date.today()+timedelta(days=7)),'passenger_count':1,'adults_count':1,'minors_count':0},201)
                record('request',code=req['code'])
                candidate=req['candidates'][0]['request_partner_id']
                if scenario=='counter_offer':
                    call('POST','/partner-responses','partner',{'request_partner_id':candidate,'action':'counter_offer','proposed_price':'120.00','proposed_currency':'PEN','partner_message':'Contraoferta DEMO'})
                    record('counter_offer',amount='120.00')
                    offer=call('GET','/partner-responses/'+candidate+'/counter-offer','tourist')
                    call('POST','/partner-responses/'+candidate+'/accept-counter-offer','tourist',{'expected_version':offer['version']})
                    record('tourist_accepts_counter_offer',amount='120.00')
                else:
                    call('POST','/partner-responses','partner',{'request_partner_id':candidate,'action':'accept'})
                record('accepted')
                reservation=call('POST','/reservations','tourist',{'service_request_code':req['code']},201)
                record('reservation',code=reservation['code'])
                call('POST','/reservations/'+reservation['code']+'/passengers','tourist',{'passengers':[{
                    'passenger_number':1,'first_name':'Turista','last_name':'DEMO','birth_date':'1990-01-01','nationality_code':'PE',
                    'document_type':'passport','document_number':'DEMO-'+suffix,'is_primary_passenger':True}]},201)
                record('passengers',count=1)
                payment=call('POST','/payments','tourist',{'reservation_code':reservation['code'],'payment_method':'cash','received_by':'partner'},201)
                call('POST','/payments/'+payment['code']+'/confirm-partner','partner')
                confirmation=call('POST','/payments/'+payment['code']+'/confirm-customer','tourist')
                if not confirmation.get('paid'): raise DemoFailure('Payment not confirmed')
                record('simulated_payment',code=payment['code'],amount=str(payment['amount']),currency='PEN')
                record('confirmed')
                commission=call('POST','/commissions/from-payment/'+payment['code'],'admin',expected=201)
                expected=Decimal('12') if scenario=='counter_offer' else Decimal('10')
                if Decimal(str(commission['commission_amount']))!=expected: raise DemoFailure('Commission mismatch')
                record('simulated_commission',amount=str(commission['commission_amount']))
                with get_connection() as conn: today=conn.execute('SELECT CURRENT_DATE').fetchone()[0]
                settlement=call('POST','/settlements','admin',{'partner_code':code,'period_start':str(today),'period_end':str(today),'due_date':str(today),'currency':'PEN'},201)
                call('POST','/settlements/'+settlement['code']+'/report-payment','partner',{'payment_method':'bank_transfer','payment_reference':'SIMULATED-NO-TRANSFER'})
                verified=call('POST','/settlements/'+settlement['code']+'/verify-payment','admin')
                if not verified.get('paid') or Decimal(str(verified['total_commission_amount']))!=expected: raise DemoFailure('Settlement mismatch')
                record('simulated_settlement',code=settlement['code'],amount=str(verified['total_commission_amount']),status=verified['status'])
                templates={kind:{'name':'demo_'+kind.replace('.','_'),'language':'es'} for kind in ('request.created','partner.request','partner.response','reservation.confirmed','payment.confirmed','reservation.cancelled')}
                # Materialize only this run's events; never consume older demo runs' pending facts.
                with get_connection() as conn, conn.cursor() as cur:
                    cur.execute('SELECT id,kind,traveler_id,partner_id FROM notification_events WHERE service_request_id=%s AND processed_at IS NULL FOR UPDATE',(req['id'],))
                    for eid,kind,event_traveler,event_partner in cur.fetchall():
                        for tid,target in zip(threads,(str(traveler),partner['id'])):
                            if target not in (str(event_traveler),str(event_partner)): continue
                            messaging.enqueue(cur,tid,'demo:'+str(eid)+':'+str(tid),'template',templates[kind],eid)
                        cur.execute('UPDATE notification_events SET processed_at=clock_timestamp() WHERE id=%s',(eid,))
                provider=FakeProvider()
                sent=0
                for _ in range(100):
                    if not messaging.process_one(provider,account): break
                    sent+=1
                if sent==0: raise DemoFailure('No fake notifications generated')
                with get_connection() as conn, conn.cursor() as cur:
                    cur.execute('''SELECT o.id,o.provider_message_id,t.address FROM message_outbox o JOIN channel_threads t ON t.id=o.thread_id
                        WHERE t.account_id=%s AND o.status='sent' ''',(account,))
                    for oid,mid,address in cur.fetchall():
                        messaging.apply_status(cur,DeliveryStatus(mid,address,'delivered',datetime.now(timezone.utc),str(oid)),account)
                record('whatsapp_fake',messages=sent,status='delivered',external_calls=0)
                summary['status']='completed'
            finally:
                for token in tokens.values():
                    client.post('/auth/logout',headers={'Authorization':'Bearer '+token})
        with get_connection() as conn:
            conn.execute("UPDATE demo_runs SET status='completed',summary=%s,finished_at=clock_timestamp() WHERE id=%s",(Jsonb(summary),run_id))
        return summary
    except Exception as exc:
        summary['failure']=str(exc) if isinstance(exc,DemoFailure) else type(exc).__name__
        summary['status']='failed'
        with get_connection() as conn:
            conn.execute("UPDATE demo_runs SET status='failed',summary=%s,finished_at=clock_timestamp() WHERE id=%s",(Jsonb(summary),run_id))
        raise DemoFailure('Demo run failed: '+summary['failure']+'; run '+str(run_id)) from None
