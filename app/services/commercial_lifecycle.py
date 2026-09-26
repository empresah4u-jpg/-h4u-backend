"""Transactional commercial transitions. External execution never occurs here."""
import hashlib
import json
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb

from app.auth import audit_actor, prelock_partner, recheck_owner
from app.services.finance import event
from app.services.cancellation_policy import CancellationPolicy, evaluate

OPEN_RESERVATIONS = {'pending','awaiting_passenger_data','payment_pending','confirmed','ready'}


def lock_reservation(cur, code):
    # Common ordering with refunds/settlements, independent of HTTP actor role.
    cur.execute('SELECT partner_id FROM reservations WHERE code=%s', (code,))
    row = cur.fetchone()
    if not row: raise HTTPException(404, 'Reserva no encontrada.')
    cur.execute('SELECT id FROM partners WHERE id=%s FOR UPDATE', (row[0],))
    prelock_partner(cur, 'reservation', code)
    cur.execute('''SELECT id,status,product_id,partner_id,service_at,currency,service_request_id,
                   expires_at FROM reservations WHERE code=%s FOR UPDATE''', (code,))
    row = cur.fetchone()
    recheck_owner(cur, 'reservation', code)
    return row


def require_unexpired(cur, table, identifier):
    if table not in {'service_requests','request_partners','reservations','payments'}:
        raise ValueError('Invalid deadline entity')
    cur.execute(f'SELECT expires_at IS NOT NULL AND expires_at<=clock_timestamp() FROM {table} WHERE id=%s', (identifier,))
    row = cur.fetchone()
    if row and row[0]: raise HTTPException(409, 'La operación ha vencido.')


def slot_for_reservation(cur, product, partner, day, start, quantity):
    cur.execute('''SELECT (clock_timestamp() AT TIME ZONE d.timezone)::date,
        CASE WHEN %s::time IS NULL THEN NULL ELSE (%s::date+%s::time) AT TIME ZONE d.timezone END<=clock_timestamp()
        FROM products p JOIN destinations d ON d.id=p.destination_id WHERE p.id=%s''',(start,day,start,product))
    temporal=cur.fetchone()
    if temporal and (day<temporal[0] or temporal[1]):
        raise HTTPException(409, 'La fecha/hora del servicio ya pasó.')
    cur.execute('SELECT scheduled_only FROM commercial_product_settings WHERE product_id=%s FOR SHARE', (product,))
    config = cur.fetchone()
    cur.execute('''SELECT id,active,capacity-reserved FROM commercial_slots
        WHERE product_id=%s AND partner_id=%s AND service_date=%s AND service_time=%s FOR UPDATE''',
        (product,partner,day,start))
    slot = cur.fetchone()
    if not slot:
        cur.execute('SELECT EXISTS(SELECT 1 FROM commercial_slots WHERE product_id=%s AND partner_id=%s)', (product,partner))
        has_inventory = cur.fetchone()[0]
        if has_inventory or (config and config[0]):
            raise HTTPException(409, 'No hay salida configurada para la fecha/hora.')
        return None
    if not slot[1] or slot[2] < quantity: raise HTTPException(409, 'No hay cupos suficientes.')
    return slot[0]


def cancel_reservation(cur, code, reason, key, *, policy_override=None, resolves_case_id=None):
    reason = reason.strip()
    if not reason or not key.strip(): raise HTTPException(400,'Motivo y clave de idempotencia requeridos.')
    r = lock_reservation(cur, code)
    rid,status,product,partner,service_at,currency,request_id,_ = r
    subject,role = audit_actor()
    fingerprint = hashlib.sha256(json.dumps({'reason':reason,'actor':subject,'role':role,'override_policy':str(policy_override[0]) if policy_override else None,'resolves_case_id':str(resolves_case_id) if resolves_case_id else None},sort_keys=True).encode()).hexdigest()
    cur.execute('SELECT fingerprint,result FROM cancellation_cases WHERE reservation_id=%s AND idempotency_key=%s',(rid,key))
    replay = cur.fetchone()
    if replay:
        if replay[0] != fingerprint: raise HTTPException(409,'La clave corresponde a otra cancelación.')
        return replay[1]
    if status not in OPEN_RESERVATIONS: raise HTTPException(409,'Estado incompatible con cancelación.')
    cur.execute('''SELECT v.id,v.rules FROM cancellation_policy_assignments a
        JOIN cancellation_policy_versions v ON v.id=a.policy_id
        WHERE a.product_id=%s AND (a.partner_id=%s OR a.partner_id IS NULL) AND a.active
        ORDER BY a.partner_id NULLS LAST LIMIT 1 FOR SHARE OF a''',(product,partner))
    policy = policy_override or cur.fetchone()
    cur.execute('''SELECT id,amount,status,currency FROM payments WHERE reservation_id=%s
        AND status IN ('paid','partially_refunded','refunded','disputed') ORDER BY id FOR UPDATE''',(rid,))
    payments = cur.fetchall()
    paid = Decimal('0'); refunded = Decimal('0')
    if len(payments)==1:
        pid,paid,_,_ = payments[0]
        cur.execute("SELECT COALESCE(sum(amount),0) FROM refunds WHERE payment_id=%s AND status='processed'",(pid,))
        refunded = cur.fetchone()[0]
    cur.execute('SELECT clock_timestamp()')
    now = cur.fetchone()[0]
    decision = evaluate(CancellationPolicy.model_validate(policy[1]) if policy else None,
        actor=role,now=now,service_at=service_at,paid=paid,refunded=refunded,currency=currency.strip())
    if len(payments)>1 or any(p[2]=='disputed' for p in payments):
        from app.services.cancellation_policy import CancellationDecision
        decision=CancellationDecision(outcome='requires_manual_review',reason='payment_reconciliation_required')
    case_id = uuid4()
    outcome = {'cancel':'cancelled','deny':'denied','requires_manual_review':'requires_manual_review'}[decision.outcome]
    result = jsonable_encoder(dict(cancellation_id=str(case_id),reservation_code=code,
        status=outcome,previous_status=status,decision=decision.model_dump(),refund_execution='not_required',
        resolves_case_id=str(resolves_case_id) if resolves_case_id else None))
    command_id = None
    if decision.outcome=='cancel' and decision.refund_amount>0:
        command_id=uuid4()
        result.update(refund_execution='pending',refund_command_id=str(command_id))
    cur.execute('''INSERT INTO cancellation_cases(id,reservation_id,idempotency_key,fingerprint,reason,
        actor_subject,actor_role,policy_id,policy_snapshot,status,decision,result)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
        (case_id,rid,key,fingerprint,reason,subject,role,policy[0] if policy else None,
         Jsonb(policy[1]) if policy else None,outcome,Jsonb(jsonable_encoder(decision)),Jsonb(result)))
    if decision.outcome=='cancel':
        cur.execute("UPDATE payments SET status='cancelled',updated_at=now() WHERE reservation_id=%s AND status IN ('pending','reported','waiting_verification')",(rid,))
        cur.execute("UPDATE reservations SET status='cancelled',cancelled_at=clock_timestamp(),cancellation_reason=%s,updated_at=now() WHERE id=%s",(reason,rid))
        if command_id:
            cur.execute('''INSERT INTO refund_commands(id,cancellation_id,payment_id,amount,currency)
                VALUES (%s,%s,%s,%s,%s)''',(command_id,case_id,payments[0][0],decision.refund_amount,currency))
    event(cur,'reservation',rid,'cancellation_evaluated',dict(case_id=str(case_id),previous_status=status,
          new_status='cancelled' if decision.outcome=='cancel' else status,reason=reason,decision=jsonable_encoder(decision)))
    return result


def mark_service(cur,code,target,reason):
    if target not in {'completed','no_show'}: raise ValueError('Invalid service outcome')
    r=lock_reservation(cur,code)
    rid,status,_,_,service_at,_,_,_=r
    if status==target: return {'reservation_code':code,'status':target,'already_applied':True}
    if status not in {'confirmed','ready'}: raise HTTPException(409,'Estado incompatible con resultado del servicio.')
    cur.execute('SELECT clock_timestamp()')
    if service_at is None or service_at>cur.fetchone()[0]:
        raise HTTPException(409,'Falta una hora de servicio verificable o todavía no ha llegado.')
    if not reason.strip(): raise HTTPException(400,'Se requiere evidencia o motivo.')
    column='completed_at' if target=='completed' else 'no_show_at'
    cur.execute(f'UPDATE reservations SET status=%s,{column}=clock_timestamp(),updated_at=now() WHERE id=%s',(target,rid))
    event(cur,'reservation',rid,target,dict(previous_status=status,new_status=target,reason=reason,financial_effect='none'))
    return {'reservation_code':code,'status':target,'already_applied':False}


def cancel_request(cur,code,reason):
    if not reason.strip(): raise HTTPException(400,'Motivo requerido.')
    prelock_partner(cur,'request',code)
    cur.execute('SELECT id,status FROM service_requests WHERE code=%s FOR UPDATE',(code,))
    row=cur.fetchone()
    if not row: raise HTTPException(404,'Solicitud no encontrada.')
    recheck_owner(cur,'request',code)
    rid,status=row
    if status=='cancelled': return {'code':code,'status':status,'already_applied':True}
    if status in {'expired','confirmed'}: raise HTTPException(409,'Estado terminal incompatible.')
    cur.execute('SELECT id FROM reservations WHERE service_request_id=%s',(rid,))
    if cur.fetchone(): raise HTTPException(409,'Debe cancelar la reserva mediante su política.')
    cur.execute("UPDATE service_requests SET status='cancelled',updated_at=now() WHERE id=%s",(rid,))
    cur.execute("UPDATE request_partners SET status='cancelled',is_winner=false,updated_at=now() WHERE service_request_id=%s AND status NOT IN ('rejected','lost','expired','cancelled')",(rid,))
    event(cur,'service_request',rid,'cancelled',dict(previous_status=status,new_status='cancelled',reason=reason))
    return {'code':code,'status':'cancelled','already_applied':False}
