"""Explicit capabilities for policies, inventory and audited transitions."""
from typing import Optional
from datetime import date, time
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field
from psycopg.types.json import Jsonb

from app.db import get_connection
from app.auth import authorize, audit_actor
from app.services import commercial_lifecycle as lifecycle
from app.services.cancellation_policy import CancellationPolicy
from app.services.finance import event

router=APIRouter(tags=['Commercial lifecycle'])


class Reason(BaseModel):
    model_config=ConfigDict(extra='forbid')
    reason: str=Field(min_length=1,max_length=1000)


class CancellationRequest(Reason):
    idempotency_key: str=Field(min_length=1,max_length=200)


class PolicyCreate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name: str=Field(min_length=1,max_length=120)
    policy: CancellationPolicy


class PolicyAssignment(BaseModel):
    model_config=ConfigDict(extra='forbid')
    product_id: UUID
    partner_id: Optional[UUID]=None
    policy_id: UUID
    active: bool=True


class Settings(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_ttl_seconds: Optional[int]=Field(default=None,gt=0,le=2147483647)
    offer_ttl_seconds: Optional[int]=Field(default=None,gt=0,le=2147483647)
    reservation_ttl_seconds: Optional[int]=Field(default=None,gt=0,le=2147483647)
    payment_ttl_seconds: Optional[int]=Field(default=None,gt=0,le=2147483647)
    scheduled_only: bool=False


class SlotCreate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    product_id: UUID
    partner_id: UUID
    service_date: date
    service_time: time
    capacity: int=Field(ge=0,le=2147483647)
    active: bool=True


@router.post('/reservations/{reservation_code}/cancellation-requests',dependencies=[authorize('reservation.cancel')])
def request_cancellation(reservation_code: str,payload: CancellationRequest):
    with get_connection() as conn:
        with conn.cursor() as cur:
            result=lifecycle.cancel_reservation(cur,reservation_code,payload.reason,payload.idempotency_key)
        conn.commit()
    return result


@router.post('/service-requests/{service_request_code}/cancel',dependencies=[authorize('request.cancel')])
def cancel_request(service_request_code: str,payload: Reason):
    with get_connection() as conn:
        with conn.cursor() as cur: result=lifecycle.cancel_request(cur,service_request_code,payload.reason)
        conn.commit()
    return result


@router.post('/reservations/{reservation_code}/complete',dependencies=[authorize('reservation.outcome')])
def complete(reservation_code: str,payload: Reason):
    return outcome(reservation_code,payload,'completed')


@router.post('/reservations/{reservation_code}/no-show',dependencies=[authorize('reservation.outcome')])
def no_show(reservation_code: str,payload: Reason):
    return outcome(reservation_code,payload,'no_show')


def outcome(code,payload,target):
    with get_connection() as conn:
        with conn.cursor() as cur: result=lifecycle.mark_service(cur,code,target,payload.reason)
        conn.commit()
    return result


@router.post('/admin/cancellation-policies',dependencies=[authorize('lifecycle.configure')],status_code=201)
def create_policy(payload: PolicyCreate):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute('''INSERT INTO cancellation_policy_versions(name,rules,actor_subject)
                VALUES (%s,%s,%s) RETURNING id''',(payload.name,Jsonb(payload.policy.model_dump(mode='json')),audit_actor()[0]))
            pid=cur.fetchone()[0]
            event(cur,'cancellation_policy',pid,'created',payload.model_dump(mode='json'))
        conn.commit()
    return {'id':str(pid),'immutable':True}


@router.post('/admin/cancellation-policy-assignments',dependencies=[authorize('lifecycle.configure')])
def assign_policy(payload: PolicyAssignment):
    with get_connection() as conn:
        with conn.cursor() as cur:
            if payload.partner_id:
                cur.execute('SELECT id FROM partners WHERE id=%s FOR UPDATE',(payload.partner_id,))
                if not cur.fetchone(): raise HTTPException(404,'Partner no encontrado.')
            cur.execute('SELECT id FROM products WHERE id=%s FOR UPDATE',(payload.product_id,))
            if not cur.fetchone(): raise HTTPException(404,'Producto no encontrado.')
            cur.execute('SELECT id FROM cancellation_policy_versions WHERE id=%s',(payload.policy_id,))
            if not cur.fetchone(): raise HTTPException(404,'Política no encontrada.')
            cur.execute('''SELECT id FROM cancellation_policy_assignments
                WHERE product_id=%s AND partner_id IS NOT DISTINCT FROM %s FOR UPDATE''',(payload.product_id,payload.partner_id))
            row=cur.fetchone()
            if row:
                pid=row[0]
                cur.execute('UPDATE cancellation_policy_assignments SET policy_id=%s,active=%s,updated_at=clock_timestamp() WHERE id=%s',(payload.policy_id,payload.active,pid))
            else:
                cur.execute('INSERT INTO cancellation_policy_assignments(product_id,partner_id,policy_id,active) VALUES (%s,%s,%s,%s) RETURNING id',(payload.product_id,payload.partner_id,payload.policy_id,payload.active))
                pid=cur.fetchone()[0]
            event(cur,'policy_assignment',pid,'configured',payload.model_dump(mode='json'))
        conn.commit()
    return {'id':str(pid),'active':payload.active}


@router.put('/admin/products/{product_id}/commercial-settings',dependencies=[authorize('lifecycle.configure')])
def configure_product(product_id: UUID,payload: Settings):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM products WHERE id=%s FOR UPDATE',(product_id,))
            if not cur.fetchone(): raise HTTPException(404,'Producto no encontrado.')
            cur.execute('''INSERT INTO commercial_product_settings(product_id,request_ttl_seconds,offer_ttl_seconds,
                reservation_ttl_seconds,payment_ttl_seconds,scheduled_only) VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT(product_id) DO UPDATE SET request_ttl_seconds=excluded.request_ttl_seconds,
                offer_ttl_seconds=excluded.offer_ttl_seconds,reservation_ttl_seconds=excluded.reservation_ttl_seconds,
                payment_ttl_seconds=excluded.payment_ttl_seconds,scheduled_only=excluded.scheduled_only,updated_at=clock_timestamp()''',
                (product_id,payload.request_ttl_seconds,payload.offer_ttl_seconds,payload.reservation_ttl_seconds,payload.payment_ttl_seconds,payload.scheduled_only))
            event(cur,'product',product_id,'commercial_settings',payload.model_dump())
        conn.commit()
    return {'product_id':str(product_id),**payload.model_dump()}


@router.post('/admin/commercial-slots',dependencies=[authorize('lifecycle.configure')])
def configure_slot(payload: SlotCreate):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM partners WHERE id=%s FOR UPDATE',(payload.partner_id,))
            if not cur.fetchone(): raise HTTPException(404,'Partner no encontrado.')
            cur.execute('SELECT id FROM product_partners WHERE product_id=%s AND partner_id=%s',(payload.product_id,payload.partner_id))
            if not cur.fetchone(): raise HTTPException(404,'Vínculo comercial no encontrado.')
            # Never retroactively introduce inventory over existing unallocated reservations.
            cur.execute('''SELECT id FROM reservations WHERE product_id=%s AND partner_id=%s AND service_date=%s
                AND service_time=%s AND slot_id IS NULL AND status NOT IN ('cancelled','expired') LIMIT 1''',
                (payload.product_id,payload.partner_id,payload.service_date,payload.service_time))
            if cur.fetchone(): raise HTTPException(409,'Existen reservas sin asignación; requiere conciliación explícita.')
            cur.execute('''SELECT id,reserved FROM commercial_slots WHERE product_id=%s AND partner_id=%s
                AND service_date=%s AND service_time=%s FOR UPDATE''',
                (payload.product_id,payload.partner_id,payload.service_date,payload.service_time))
            row=cur.fetchone()
            if row:
                if payload.capacity<row[1]: raise HTTPException(409,'Capacidad menor que cupos ocupados.')
                sid=row[0]
                cur.execute('UPDATE commercial_slots SET capacity=%s,active=%s WHERE id=%s',(payload.capacity,payload.active,sid))
            else:
                cur.execute('''INSERT INTO commercial_slots(product_id,partner_id,service_date,service_time,capacity,active)
                    VALUES (%s,%s,%s,%s,%s,%s) RETURNING id''',tuple(payload.model_dump().values()))
                sid=cur.fetchone()[0]
            event(cur,'commercial_slot',sid,'configured',payload.model_dump(mode='json'))
        conn.commit()
    return {'id':str(sid)}


class RefundConfirmation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    external_reference: str=Field(min_length=1,max_length=200)


@router.post('/admin/refund-commands/{command_id}/confirm',dependencies=[authorize('lifecycle.configure')])
def confirm_refund_command(command_id: UUID,payload: RefundConfirmation):
    from app.services.refund_execution import confirm
    with get_connection() as conn:
        with conn.cursor() as cur: result=confirm(cur,command_id,payload.external_reference)
        conn.commit()
    return result


@router.get('/admin/cancellation-cases/{case_id}',dependencies=[authorize('lifecycle.configure')])
def cancellation_case(case_id: UUID):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT policy_id,policy_snapshot,decision,result FROM cancellation_cases WHERE id=%s',(case_id,))
            row=cur.fetchone()
            if not row: raise HTTPException(404,'Caso no encontrado.')
    return dict(policy_id=row[0],policy_snapshot=row[1],decision=row[2],result=row[3])


class ReviewResolution(CancellationRequest):
    policy_id: UUID


@router.post('/admin/cancellation-cases/{case_id}/resolve',dependencies=[authorize('lifecycle.configure')])
def resolve_cancellation(case_id: UUID,payload: ReviewResolution):
    """Apply an explicit immutable policy version to this case only, never globally."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT r.code,c.status FROM cancellation_cases c
                JOIN reservations r ON r.id=c.reservation_id WHERE c.id=%s''',(case_id,))
            case=cur.fetchone()
            if not case: raise HTTPException(404,'Caso no encontrado.')
            if case[1]!='requires_manual_review': raise HTTPException(409,'El caso no requiere revisión.')
            cur.execute('SELECT id,rules FROM cancellation_policy_versions WHERE id=%s',(payload.policy_id,))
            policy=cur.fetchone()
            if not policy: raise HTTPException(404,'Política no encontrada.')
            result=lifecycle.cancel_reservation(cur,case[0],payload.reason,payload.idempotency_key,
                policy_override=policy,resolves_case_id=case_id)
        conn.commit()
    return result
