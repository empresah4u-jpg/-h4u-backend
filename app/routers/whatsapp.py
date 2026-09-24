"""Signed webhook and tightly scoped administrative diagnostics."""
import hashlib
import hmac
import json
from typing import Optional
from uuid import UUID
from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from app.admin_policy import require_capability
from app.auth import Principal
from app.db import get_connection
from app.messaging.provider import Settings
from app.messaging.normalization import normalize
from app.messaging import service

router=APIRouter(tags=['WhatsApp'])
MAX_BODY=256*1024


def configuration():
    settings=Settings.from_env()
    if not (settings.account_id and settings.verify_token and settings.app_secret):
        raise HTTPException(503,'Canal no configurado.')
    return settings


@router.get('/webhooks/whatsapp',response_class=PlainTextResponse)
def verify(request: Request):
    params=request.query_params
    request.scope['query_string']=b''  # Prevent verification tokens in Uvicorn access logs.
    settings=configuration()
    token=params.get('hub.verify_token','')
    if params.get('hub.mode')!='subscribe' or not hmac.compare_digest(token.encode(),settings.verify_token.encode()):
        raise HTTPException(403,'Verificación inválida.')
    challenge=params.get('hub.challenge','')
    if not challenge.isascii() or not challenge.isdigit() or len(challenge)>200:
        raise HTTPException(400,'Challenge inválido.')
    return PlainTextResponse(challenge,headers={'Cache-Control':'no-store'})


@router.post('/webhooks/whatsapp')
async def webhook(request: Request):
    settings=configuration()
    raw=bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw)>MAX_BODY: raise HTTPException(413,'Evento demasiado grande.')
    signature=request.headers.get('x-hub-signature-256','')
    expected='sha256='+hmac.new(settings.app_secret.encode(),bytes(raw),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature.encode(),expected.encode()): raise HTTPException(403,'Firma inválida.')
    try:
        payload=json.loads(raw)
        inbound,statuses=normalize(payload,settings.account_id)
    except (ValueError,KeyError,TypeError,AttributeError,RecursionError):
        raise HTTPException(400,'Evento inválido.') from None
    await run_in_threadpool(service.receive,inbound,statuses,settings.account_id)
    return {'received':True}


class Binding(BaseModel):
    model_config=ConfigDict(extra='forbid')
    traveler_id: Optional[UUID]=None
    partner_id: Optional[UUID]=None
    opted_in: bool
    reason: str=Field(min_length=3,max_length=500)


class Reply(BaseModel):
    model_config=ConfigDict(extra='forbid')
    idempotency_key: str=Field(min_length=1,max_length=220)
    text: str=Field(min_length=1,max_length=4096)


@router.put('/admin/messaging/threads/{thread_id}/binding')
def bind(thread_id: UUID,payload: Binding,actor: Principal=require_capability('manage')):
    return service.bind_thread(actor,thread_id,payload.traveler_id,payload.partner_id,payload.opted_in,payload.reason)


@router.post('/admin/messaging/threads/{thread_id}/replies',status_code=202)
def reply(thread_id: UUID,payload: Reply,actor: Principal=require_capability('manage')):
    return service.queue_reply(actor,thread_id,payload.idempotency_key,payload.text)


@router.get('/admin/messaging/outbox')
def diagnostics(actor: Principal=require_capability('operations.read'),status: Optional[str]=Query(None,pattern='^(pending|processing|sent|delivered|read|failed)$'),
                limit: int=Query(50,ge=1,le=100),offset: int=Query(0,ge=0)):
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('''SELECT id,thread_id,event_id,message_type,status,attempt_count,next_attempt_at,last_error,created_at
            FROM message_outbox WHERE (%s::text IS NULL OR status=%s) ORDER BY created_at DESC,id LIMIT %s OFFSET %s''',(status,status,limit,offset))
        names=[c.name for c in cur.description]
        return {'items':[dict(zip(names,row)) for row in cur.fetchall()],'limit':limit,'offset':offset}


@router.get('/admin/messaging/threads')
def threads(actor: Principal=require_capability('users.read'),limit: int=Query(50,ge=1,le=100),offset: int=Query(0,ge=0)):
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('''SELECT id,traveler_id,partner_id,opted_in_at,last_inbound_at,created_at
            FROM channel_threads ORDER BY created_at DESC,id LIMIT %s OFFSET %s''',(limit,offset))
        names=[c.name for c in cur.description]
        return {'items':[dict(zip(names,row)) for row in cur.fetchall()],'limit':limit,'offset':offset}


@router.get('/admin/messaging/threads/{thread_id}')
def thread_detail(thread_id: UUID,actor: Principal=require_capability('users.read')):
    # Destination is necessary for an administrator to verify binding/consent out of band.
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('''SELECT id,address,traveler_id,partner_id,opted_in_at,last_inbound_at
            FROM channel_threads WHERE id=%s''',(thread_id,))
        row=cur.fetchone()
        if not row: raise HTTPException(404,'Conversación no encontrada.')
        return dict(zip((c.name for c in cur.description),row))
