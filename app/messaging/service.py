"""Persistence, consent and outbox. No external I/O in database transactions."""
from datetime import datetime, timezone, timedelta
import re
from uuid import UUID
from fastapi import HTTPException
from psycopg.types.json import Jsonb
from app.db import get_connection
from app.services.partner_memberships import require_admin
from app.services.admin_governance import governance_lock
from app.messaging.provider import SendError

RANK={'pending':0,'processing':0,'failed':1,'sent':2,'delivered':3,'read':4}


def receive(inbound, statuses, account):
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout='5s'")
        cur.execute("SET LOCAL lock_timeout='2s'")
        for msg in inbound:
            cur.execute('''INSERT INTO channel_threads(account_id,address) VALUES (%s,%s)
                ON CONFLICT(provider,account_id,address) DO UPDATE SET address=EXCLUDED.address RETURNING id''',(account,msg.sender))
            thread=cur.fetchone()[0]
            cur.execute('''INSERT INTO inbound_messages(thread_id,provider_message_id,message_type,text,occurred_at)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id''',(thread,msg.external_id,msg.message_type,msg.text,msg.timestamp))
            if cur.fetchone():
                cur.execute('UPDATE channel_threads SET last_inbound_at=GREATEST(last_inbound_at,%s) WHERE id=%s',(msg.timestamp,thread))
        for status in statuses: apply_status(cur,status,account)


def apply_status(cur, event, account):
    # Callback UUID is correlation only, authenticated by the signed webhook and account/recipient.
    try: correlation=UUID(event.correlation_id) if event.correlation_id else None
    except ValueError: correlation=None
    cur.execute('''SELECT o.id,o.status,o.provider_message_id,o.last_error FROM message_outbox o
        JOIN channel_threads t ON t.id=o.thread_id WHERE t.account_id=%s AND t.address=%s
        AND (o.provider_message_id=%s OR (o.id=%s AND o.attempt_count>0)) FOR UPDATE OF o''',
        (account,event.recipient,event.external_id,correlation))
    rows=cur.fetchall()
    if len(rows)!=1: return
    oid,current,mid,last_error=rows[0]
    if mid and mid!=event.external_id: return
    if event.status=='failed':
        if current in {'delivered','read','failed'}: return
    elif RANK[event.status]<=RANK[current] or (last_error=='provider_delivery_failed' and event.status=='sent'): return
    cur.execute('''UPDATE message_outbox SET status=%s,provider_message_id=%s,
        sent_at=CASE WHEN %s IN ('sent','delivered','read') THEN COALESCE(sent_at,%s) ELSE sent_at END,
        delivered_at=CASE WHEN %s IN ('delivered','read') THEN COALESCE(delivered_at,%s) ELSE delivered_at END,
        read_at=CASE WHEN %s='read' THEN COALESCE(read_at,%s) ELSE read_at END,
        failed_at=CASE WHEN %s='failed' THEN COALESCE(failed_at,%s) ELSE failed_at END,
        last_error=CASE WHEN %s='failed' THEN 'provider_delivery_failed' ELSE NULL END WHERE id=%s''',
        (event.status,event.external_id,event.status,event.timestamp,event.status,event.timestamp,
         event.status,event.timestamp,event.status,event.timestamp,event.status,oid))


def bind_thread(actor, thread_id, traveler_id, partner_id, consent, reason):
    # Explicit H4U-admin assertion of verified destination + opt-in, never phone-based authentication.
    if bool(traveler_id)==bool(partner_id): raise HTTPException(422,'Seleccione traveler o partner.')
    with get_connection() as conn, conn.cursor() as cur:
        governance_lock(cur); require_admin(cur,actor)
        cur.execute('SELECT traveler_id,partner_id FROM channel_threads WHERE id=%s FOR UPDATE',(thread_id,))
        old=cur.fetchone()
        if not old: raise HTTPException(404,'Conversación no encontrada.')
        new=(traveler_id,partner_id)
        if any(old) and old!=new: raise HTTPException(409,'No se permite reasignar una identidad vinculada.')
        table='travelers' if traveler_id else 'partners'
        cur.execute('SELECT id FROM '+table+' WHERE id=%s FOR SHARE',(traveler_id or partner_id,))
        if not cur.fetchone(): raise HTTPException(404,'Entidad no encontrada.')
        cur.execute('''UPDATE channel_threads SET traveler_id=%s,partner_id=%s,bound_by=%s,binding_reason=%s,
            opted_in_at=CASE WHEN %s THEN clock_timestamp() ELSE NULL END WHERE id=%s''',
            (traveler_id,partner_id,actor.subject,reason,consent,thread_id))
        cur.execute('''INSERT INTO channel_binding_events(thread_id,actor_id,consent,reason)
            VALUES (%s,%s,%s,%s)''',(thread_id,actor.subject,consent,reason))
    return {'id':str(thread_id),'opted_in':consent}


def enqueue(cur, thread_id, key, kind, content, event_id=None):
    if not 1<=len(key)<=220 or kind not in {'text','template'}: raise HTTPException(422,'Mensaje inválido.')
    if kind=='text':
        if set(content)!={'text'} or not isinstance(content['text'],str) or not 1<=len(content['text'])<=4096: raise HTTPException(422,'Texto inválido.')
    elif (set(content)!={'name','language'} or not re.fullmatch(r'[a-z0-9_]{1,100}',content.get('name',''))
          or not re.fullmatch(r'[a-z]{2,3}(?:_[A-Z]{2})?',content.get('language',''))): raise HTTPException(422,'Template inválido.')
    cur.execute('SELECT id FROM channel_threads WHERE id=%s FOR SHARE',(thread_id,))
    if not cur.fetchone(): raise HTTPException(404,'Conversación no encontrada.')
    cur.execute('''INSERT INTO message_outbox(thread_id,idempotency_key,message_type,content,event_id)
        VALUES (%s,%s,%s,%s,%s) ON CONFLICT(idempotency_key) DO NOTHING RETURNING id''',(thread_id,key,kind,Jsonb(content),event_id))
    row=cur.fetchone()
    if row: return str(row[0])
    cur.execute('SELECT id,thread_id,message_type,content FROM message_outbox WHERE idempotency_key=%s',(key,))
    row=cur.fetchone()
    if (str(row[1]),row[2],row[3])!=(str(thread_id),kind,content): raise HTTPException(409,'Clave reutilizada con otro mensaje.')
    return str(row[0])


def queue_reply(actor, thread_id, key, text):
    with get_connection() as conn, conn.cursor() as cur:
        require_admin(cur,actor)
        oid=enqueue(cur,thread_id,key,'text',{'text':text})
    return {'id':oid,'queued':True}


def materialize(templates, account, limit=100):
    """Templates map approved event kind -> {name,language}; no historical catch-up without consent."""
    count=0
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('''SELECT id,kind,traveler_id,partner_id,created_at FROM notification_events
            WHERE processed_at IS NULL AND kind=ANY(%s) ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT %s''',(list(templates),limit))
        for eid,kind,traveler,partner,created in cur.fetchall():
            if kind not in templates: continue  # Remains pending until explicitly configured.
            cur.execute('''SELECT id FROM channel_threads WHERE account_id=%s AND opted_in_at IS NOT NULL
                AND opted_in_at<=%s AND ((traveler_id=%s AND traveler_id IS NOT NULL)
                OR (partner_id=%s AND partner_id IS NOT NULL))''',(account,created,traveler,partner))
            for (thread,) in cur.fetchall():
                enqueue(cur,thread,'event:'+str(eid)+':'+str(thread),'template',templates[kind],eid)
                count+=1
            cur.execute('UPDATE notification_events SET processed_at=clock_timestamp() WHERE id=%s',(eid,))
    return count


def process_one(provider, account):
    """Claim/commit -> HTTP -> acknowledge. Ambiguous/crashed sends are never blindly retried."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('''UPDATE message_outbox SET status='failed',last_error='delivery_unknown',failed_at=clock_timestamp()
            WHERE status='processing' AND processing_at<clock_timestamp()-interval '5 minutes'
            AND thread_id IN (SELECT id FROM channel_threads WHERE account_id=%s)''',(account,))
        cur.execute('''SELECT o.id,o.thread_id,o.message_type,o.content,o.attempt_count
            FROM message_outbox o JOIN channel_threads t ON t.id=o.thread_id
            WHERE o.status='pending' AND o.attempt_count<5 AND o.next_attempt_at<=clock_timestamp()
            AND t.account_id=%s ORDER BY o.next_attempt_at,o.id FOR UPDATE OF o SKIP LOCKED LIMIT 1''',(account,))
        row=cur.fetchone()
        if not row: return False
        oid,thread,kind,content,attempt=row
        cur.execute('SELECT address,last_inbound_at,opted_in_at FROM channel_threads WHERE id=%s',(thread,))
        address,last_inbound,consent=cur.fetchone()
        now=datetime.now(timezone.utc)
        if (kind=='template' and consent is None) or (kind=='text' and (last_inbound is None or last_inbound<now-timedelta(hours=24))):
            cur.execute("UPDATE message_outbox SET status='failed',last_error='consent_or_window_required',failed_at=clock_timestamp() WHERE id=%s",(oid,))
            return True
        cur.execute("UPDATE message_outbox SET status='processing',processing_at=clock_timestamp(),attempt_count=attempt_count+1 WHERE id=%s",(oid,))
    try:
        if kind=='text': mid=provider.send_text(address,content['text'],str(oid))
        else: mid=provider.send_template(address,content['name'],content['language'],str(oid))
    except Exception as exc:
        # Unknown adapter failures are ambiguous; never persist exception text.
        retry=isinstance(exc,SendError) and exc.retryable and attempt+1<5
        code=exc.code if isinstance(exc,SendError) and exc.code in {'connection_unavailable','rate_limited','provider_rejected','delivery_unknown'} else 'delivery_unknown'
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute('''UPDATE message_outbox SET status=%s,last_error=%s,
                next_attempt_at=clock_timestamp()+(%s * interval '1 second'),
                failed_at=CASE WHEN %s THEN NULL ELSE clock_timestamp() END
                WHERE id=%s AND status='processing' ''',('pending' if retry else 'failed',code,min(3600,30*2**attempt),retry,oid))
    else:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute('''UPDATE message_outbox SET provider_message_id=COALESCE(provider_message_id,%s),
                status=CASE WHEN status IN ('delivered','read') OR last_error='provider_delivery_failed' THEN status ELSE 'sent' END,
                sent_at=COALESCE(sent_at,clock_timestamp()),last_error=CASE WHEN last_error='provider_delivery_failed' THEN last_error ELSE NULL END
                WHERE id=%s AND (provider_message_id IS NULL OR provider_message_id=%s)''',(mid,oid,mid))
    return True
