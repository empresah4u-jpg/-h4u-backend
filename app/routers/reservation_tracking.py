"""Public, owner-scoped reservation reads. No commercial transitions."""
from datetime import date, datetime, time
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from app.auth import Principal, get_current_actor, recheck_principal
from app.db import get_connection
from app.routers.request_tracking import ProductSummary, PartnerSummary, validate_actor

router = APIRouter(prefix='/reservations', tags=['Reservations'])


class AssociatedRequest(BaseModel):
    id: UUID
    code: str


class ReservationDetail(BaseModel):
    id: UUID
    code: str
    service_request: AssociatedRequest
    product: ProductSummary
    provider: PartnerSummary
    service_date: date
    service_time: Optional[time]
    passenger_count: int
    agreed_price: Optional[str]
    currency: str
    status: str
    created_at: datetime


class ReservationPage(BaseModel):
    items: List[ReservationDetail]
    has_more: bool


SELECT = '''SELECT rv.id,rv.code,sr.id,sr.code,p.id,p.code,p.name,partner.business_name,
 rv.service_date,rv.service_time,rv.passenger_count,rv.agreed_price::text,
 rv.currency,rv.status,rv.created_at
 FROM reservations rv JOIN service_requests sr ON sr.id=rv.service_request_id
 JOIN products p ON p.id=rv.product_id JOIN partners partner ON partner.id=rv.partner_id
 WHERE rv.traveler_id=%s AND sr.traveler_id=rv.traveler_id'''


def project(row):
    return dict(id=row[0], code=row[1], service_request=dict(id=row[2],code=row[3]),
                product=dict(id=row[4],code=row[5],name=row[6]),provider=dict(name=row[7]),
                service_date=row[8],service_time=row[9],passenger_count=row[10],
                agreed_price=row[11],currency=row[12],status=row[13],created_at=row[14])


@router.get('', response_model=ReservationPage)
def list_reservations(request: Request, response: Response, limit: int = Query(20, ge=1, le=100),
                      offset: int = Query(0, ge=0), actor: Principal = Depends(get_current_actor)):
    validate_actor(actor, request, response, {'limit','offset'})
    with get_connection() as conn, conn.cursor() as cur:
        recheck_principal(cur, actor)
        cur.execute(SELECT+' ORDER BY rv.created_at DESC,rv.id DESC LIMIT %s OFFSET %s',
                    (actor.traveler_id,limit+1,offset))
        rows=cur.fetchall()
    return dict(items=[project(row) for row in rows[:limit]],has_more=len(rows)>limit)


@router.get('/{reservation_id}', response_model=ReservationDetail)
def reservation_detail(reservation_id: UUID, request: Request, response: Response,
                       actor: Principal = Depends(get_current_actor)):
    validate_actor(actor, request, response, set())
    with get_connection() as conn, conn.cursor() as cur:
        recheck_principal(cur, actor)
        cur.execute(SELECT+' AND rv.id=%s',(actor.traveler_id,reservation_id))
        row=cur.fetchone()
    if row is None:
        raise HTTPException(404,'Reserva no encontrada.')
    return project(row)
