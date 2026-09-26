from decimal import Decimal
import hashlib
import json
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import get_connection
from app.auth import authorize, audit_actor
from app.services.finance import lock_payment_partner, adjust_for_refund, event


router = APIRouter(
    prefix="/refunds",
    tags=["Refunds"],
)


class RefundCreate(BaseModel):
    payment_code: str
    idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=200)

    amount: Decimal = Field(
        gt=0,
        decimal_places=2,
        max_digits=12,
    )

    reason: str

    refund_method: Optional[str] = None
    external_reference: Optional[str] = Field(default=None, max_length=200)


@router.post("", status_code=201, dependencies=[authorize("refund.create")])
def create_refund(payload: RefundCreate):
    with get_connection() as conn:
        with conn.cursor() as cur:
            result = create_refund_in_transaction(payload, cur)
        conn.commit()
    return result


def create_refund_in_transaction(payload: RefundCreate, cur, *, command_id=None):
    """Shared accounting operation; caller controls commit and external evidence."""
    payment_code = payload.payment_code.strip()
    reason = payload.reason.strip()
    method = payload.refund_method.strip().lower() if payload.refund_method else None
    reference = (payload.external_reference or "").strip() or None
    key = (payload.idempotency_key or "").strip() or None
    if not payment_code or not reason:
        raise HTTPException(400, "Código de pago y motivo son obligatorios.")
    if payload.idempotency_key is not None and key is None:
        raise HTTPException(400, "La clave de idempotencia no puede estar vacía.")
    if method is not None and method not in {"yape","plin","bank_transfer","card","mercado_pago","izipay","cash","other"}:
        raise HTTPException(400, "Método de reembolso no válido.")
    subject, role = audit_actor()
    if role != 'system' and key is None and reference is None:
        raise HTTPException(400, "Indique idempotency_key o external_reference para evitar duplicados.")
    fingerprint=hashlib.sha256(json.dumps({
        'amount':str(payload.amount.quantize(Decimal('0.01'))),
        'reason':reason,'method':method,'reference':reference,
    },sort_keys=True).encode()).hexdigest()
    lock_payment_partner(cur,payment_code)
    cur.execute('SELECT id,amount,currency,status,reservation_id FROM payments WHERE code=%s FOR UPDATE',(payment_code,))
    payment=cur.fetchone()
    if not payment:
        raise HTTPException(404,"Pago no encontrado.")
    payment_id, original, currency, status, reservation_id=payment
    if key:
        cur.execute('SELECT request_fingerprint,result FROM refunds WHERE payment_id=%s AND idempotency_key=%s',(payment_id,key))
        existing=cur.fetchone()
        if existing:
            if existing[0]!=fingerprint or existing[1] is None:
                raise HTTPException(409,"La clave de idempotencia ya tiene otra solicitud.")
            return existing[1]
    if reference:
        cur.execute('SELECT id FROM refunds WHERE payment_id=%s AND external_reference=%s',(payment_id,reference))
        if cur.fetchone():
            raise HTTPException(409,"La referencia de reembolso ya fue registrada para este pago.")
    if status not in {'paid','partially_refunded'}:
        raise HTTPException(409,"El pago no permite reembolso.")
    if not original.is_finite() or original<=0:
        raise HTTPException(409,"Importe original inválido.")
    cur.execute("SELECT COALESCE(sum(amount),0) FROM refunds WHERE payment_id=%s AND status='processed'",(payment_id,))
    previous=cur.fetchone()[0]
    cur.execute("SELECT COALESCE(sum(amount),0) FROM refund_commands WHERE payment_id=%s AND status='pending' AND id IS DISTINCT FROM %s", (payment_id,command_id))
    reserved = cur.fetchone()[0]
    if previous + payload.amount + reserved > original:
        raise HTTPException(409, "El saldo está reservado para una devolución pendiente.")
    total=previous+payload.amount
    if not previous.is_finite() or previous<0 or total>original:
        raise HTTPException(409,"El importe solicitado supera el saldo disponible para reembolso.")
    code='REF-'+uuid4().hex[:12].upper()
    cur.execute("""INSERT INTO refunds(code,payment_id,amount,currency,reason,status,refund_method,
        external_reference,processed_at,idempotency_key,request_fingerprint,actor_subject,actor_role)
        VALUES (%s,%s,%s,%s,%s,'processed',%s,%s,now(),%s,%s,%s,%s)
        RETURNING id,processed_at""",
        (code,payment_id,payload.amount,currency,reason,method,reference,key,fingerprint,subject,role))
    refund_id,processed_at=cur.fetchone()
    adjustment=adjust_for_refund(cur,payment_id,refund_id,original,total)
    payment_status='refunded' if total==original else 'partially_refunded'
    cur.execute('UPDATE payments SET status=%s,updated_at=now() WHERE id=%s',(payment_status,payment_id))
    result=jsonable_encoder({
        'id':str(refund_id),'code':code,'payment_code':payment_code,
        'reservation_id':str(reservation_id),'refund_amount':float(payload.amount),
        'currency':currency.strip(),'refund_status':'processed','payment_status':payment_status,
        'total_refunded':float(total),'remaining_amount':float(original-total),
        'processed_at':processed_at,'commission_adjustment':adjustment,
    })
    cur.execute('UPDATE refunds SET result=%s WHERE id=%s',(Jsonb(result),refund_id))
    event(cur,'refund',refund_id,'processed',result)
    return result
