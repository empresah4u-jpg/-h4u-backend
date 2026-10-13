"""Owner-scoped tourist read projections of the existing commercial model."""
from datetime import date, datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from app.auth import Principal, get_current_actor, recheck_principal
from app.db import get_connection

router = APIRouter(prefix='/service-requests', tags=['Service Requests'])


class ProductSummary(BaseModel):
    id: UUID
    code: str
    name: str


class PartnerSummary(BaseModel):
    name: str


class ReservationSummary(BaseModel):
    id: UUID
    code: str
    status: str


class RequestSummary(BaseModel):
    id: UUID
    code: str
    product: Optional[ProductSummary]
    destination: str
    service_date: date
    passenger_count: int
    status: str
    created_at: datetime
    selected_partner: Optional[PartnerSummary]
    reservations: List[ReservationSummary]


class RequestList(BaseModel):
    items: List[RequestSummary]
    has_more: bool


# No candidate pricing, traveler IDs, contact details, notes or consent payloads.
SELECT = """
SELECT sr.id,sr.code,p.id,p.code,p.name,d.code,sr.service_date,
       sr.passenger_count,sr.status,sr.created_at,partner.business_name,
       COALESCE((SELECT jsonb_agg(jsonb_build_object('id',r.id,'code',r.code,'status',r.status)
                              ORDER BY r.created_at,r.id)
                 FROM reservations r
                 WHERE r.service_request_id=sr.id AND r.traveler_id=sr.traveler_id),'[]'::jsonb)
FROM service_requests sr
JOIN destinations d ON d.id=sr.destination_id
LEFT JOIN products p ON p.id=sr.product_id
LEFT JOIN partners partner ON partner.id=sr.assigned_partner_id
WHERE sr.traveler_id=%s
"""


def summary(row):
    return dict(id=row[0], code=row[1], product=dict(id=row[2], code=row[3], name=row[4]) if row[2] else None,
                destination=row[5], service_date=row[6], passenger_count=row[7], status=row[8], created_at=row[9],
                selected_partner=dict(name=row[10]) if row[10] is not None else None, reservations=row[11])


def validate_actor(actor, request, response, allowed):
    response.headers['Cache-Control'] = 'no-store'
    if actor.role != 'tourist' or actor.traveler_id is None:
        raise HTTPException(403, 'Solo turistas autenticados.')
    if set(request.query_params) - allowed:
        raise HTTPException(422, 'Filtro no admitido.')


@router.get('', response_model=RequestList)
def list_requests(request: Request, response: Response, limit: int = Query(20, ge=1, le=100),
                  offset: int = Query(0, ge=0), actor: Principal = Depends(get_current_actor)):
    validate_actor(actor, request, response, {'limit', 'offset'})
    with get_connection() as conn, conn.cursor() as cur:
        recheck_principal(cur, actor)
        cur.execute(SELECT + ' ORDER BY sr.created_at DESC,sr.id DESC LIMIT %s OFFSET %s',
                    (actor.traveler_id, limit + 1, offset))
        rows = cur.fetchall()
    return dict(items=[summary(row) for row in rows[:limit]], has_more=len(rows) > limit)


@router.get('/{request_id}', response_model=RequestSummary)
def request_detail(request_id: UUID, request: Request, response: Response, actor: Principal = Depends(get_current_actor)):
    validate_actor(actor, request, response, set())
    with get_connection() as conn, conn.cursor() as cur:
        recheck_principal(cur, actor)
        cur.execute(SELECT + ' AND sr.id=%s', (actor.traveler_id, request_id))
        row = cur.fetchone()
    if row is None:
        # Foreign and nonexistent IDs are intentionally indistinguishable.
        raise HTTPException(404, 'Solicitud no encontrada.')
    return summary(row)


@router.get('/{request_id}/offers')
def request_offers(request_id: UUID, request: Request, response: Response,
                   actor: Principal = Depends(get_current_actor)):
    """Current candidate versions only; no invented historical offer rows."""
    validate_actor(actor, request, response, set())
    with get_connection() as conn, conn.cursor() as cur:
        recheck_principal(cur, actor)
        # One statement provides a consistent owner-scoped request/offer snapshot.
        cur.execute("""
        SELECT r.id, COALESCE(jsonb_agg(jsonb_build_object(
          'id',o.id,'provider',p.business_name,'amount_total',COALESCE(c.amount_total,o.proposed_price)::text,
          'currency',COALESCE(c.currency,o.proposed_currency),
          'service_date',COALESCE(c.service_date::text,o.offer_request_snapshot->>'service_date'),
          'service_time',COALESCE(c.service_time::text,o.proposed_time::text,o.offer_request_snapshot->>'preferred_time'),
          'passenger_count',COALESCE(c.passenger_count,(o.offer_request_snapshot->>'passenger_count')::int),
          'conditions',COALESCE(c.conditions,o.partner_message),
          'flexible_time',CASE WHEN c.id IS NOT NULL THEN c.flexible_time
            WHEN o.proposed_time IS NOT NULL THEN false ELSE (o.offer_request_snapshot->>'flexible_time')::boolean END,
          'adults_count',COALESCE(c.adults_count,(o.offer_request_snapshot->>'adults_count')::int),
          'minors_count',COALESCE(c.minors_count,(o.offer_request_snapshot->>'minors_count')::int),
          'request_notes',COALESCE(c.request_snapshot,o.offer_request_snapshot)->>'additional_notes',
          'offer_version',COALESCE(c.offer_version,o.offer_version),
          'request_revision',COALESCE(c.request_revision,o.offer_request_revision),
          'expires_at',LEAST(r.expires_at,o.expires_at),
          'status',CASE
            WHEN c.id IS NOT NULL THEN 'accepted'
            WHEN r.status NOT IN ('searching','offers_received') OR r.assigned_partner_id IS NOT NULL THEN 'request_closed'
            WHEN o.offer_request_revision IS DISTINCT FROM r.terms_revision
              OR o.offer_request_snapshot IS DISTINCT FROM request_consent_terms(r) THEN 'request_changed'
            WHEN LEAST(r.expires_at,o.expires_at)<=statement_timestamp() THEN 'expired'
            WHEN o.status<>'counter_offered' THEN 'obsolete'
            WHEN o.offer_version<=0 OR o.proposed_price IS NULL OR o.proposed_currency IS NULL
              OR o.offer_request_snapshot IS NULL THEN 'unknown'
            WHEN (p.status='active' AND p.reservations_enabled AND pp.status='active'
              AND product.status='active' AND product.reservations_enabled) IS NOT TRUE THEN 'unavailable'
            ELSE 'current' END
        ) ORDER BY o.id) FILTER(WHERE o.id IS NOT NULL),'[]'::jsonb)
        FROM service_requests r
        LEFT JOIN request_partners o ON o.service_request_id=r.id
          AND (o.offer_version>0 OR o.status='counter_offered')
        LEFT JOIN partners p ON p.id=o.partner_id
        LEFT JOIN product_partners pp ON pp.id=o.product_partner_id AND pp.partner_id=o.partner_id AND pp.product_id=r.product_id
        LEFT JOIN products product ON product.id=r.product_id
        LEFT JOIN request_offer_consents c ON c.request_partner_id=o.id AND c.service_request_id=r.id
        WHERE r.id=%s AND r.traveler_id=%s GROUP BY r.id
        """, (request_id, actor.traveler_id))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(404, 'Solicitud no encontrada.')
    return {'items': row[1]}
