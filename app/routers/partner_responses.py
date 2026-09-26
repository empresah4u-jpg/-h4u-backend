from datetime import datetime, time
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db import get_connection
from app.auth import authorize, recheck_owner, prelock_partner
from app.services.commercial_eligibility import require_bookable_candidate


router = APIRouter(
    prefix="/partner-responses",
    tags=["Partner Responses"],
)


class PartnerResponseCreate(BaseModel):
    request_partner_id: UUID
    action: str

    proposed_time: Optional[time] = None
    proposed_price: Optional[Decimal] = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    proposed_currency: Optional[str] = None
    partner_message: Optional[str] = None

    @field_validator("proposed_currency")
    @classmethod
    def valid_currency(cls, value):
        if value is None:
            return value
        value = value.strip().upper()
        if len(value) != 3 or not value.isascii() or not value.isalpha():
            raise ValueError("La moneda debe tener tres letras.")
        return value


@router.post("", status_code=200, dependencies=[authorize("response.create")])
def respond_to_request(payload: PartnerResponseCreate):
    return _respond_to_request(payload)


class CounterOfferAcceptance(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: datetime

    @field_validator('expected_version')
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError('La versión debe incluir zona horaria.')
        return value


@router.get('/{request_partner_id}/counter-offer', dependencies=[authorize('offer.accept')])
def read_counter_offer(request_partner_id: UUID):
    with get_connection() as conn, conn.cursor() as cur:
        recheck_owner(cur, 'candidate', request_partner_id)
        cur.execute("""SELECT proposed_price,proposed_currency,proposed_time,partner_message,updated_at
            FROM request_partners WHERE id=%s AND status='counter_offered'""",(request_partner_id,))
        row=cur.fetchone()
        if not row:
            raise HTTPException(409,'No hay una contraoferta disponible.')
        if row[0] is None or row[1] is None:
            raise HTTPException(409,'La aceptación del turista requiere precio y moneda explícitos.')
        return dict(zip(('price','currency','time','message','version'),row))


@router.post('/{request_partner_id}/accept-counter-offer', dependencies=[authorize('offer.accept')])
def accept_counter_offer(request_partner_id: UUID, payload: CounterOfferAcceptance):
    return _respond_to_request(PartnerResponseCreate(request_partner_id=request_partner_id,action='accept'),
                               expected_offer=payload.expected_version)


def _respond_to_request(payload: PartnerResponseCreate, *, expected_offer=None):

    action = payload.action.lower().strip()

    if action not in {"accept", "reject", "counter_offer"}:
        raise HTTPException(
            status_code=400,
            detail="action debe ser accept, reject o counter_offer.",
        )

    if action == "counter_offer":
        if (
            payload.proposed_time is None
            and payload.proposed_price is None
            and not payload.partner_message
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Una contraoferta debe incluir hora, "
                    "precio o mensaje."
                ),
            )

    with get_connection() as conn:
        with conn.cursor() as cur:
            if expected_offer is not None:
                # Keep partner -> request lock order also for tourist acceptance.
                cur.execute("""SELECT p.id FROM partners p JOIN request_partners rp ON rp.partner_id=p.id
                    WHERE rp.id=%s FOR SHARE OF p""", (payload.request_partner_id,))
            prelock_partner(cur, "candidate", payload.request_partner_id)

            # Obtener candidatura.
            cur.execute(
                """
                SELECT
                    rp.id,
                    rp.service_request_id,
                    rp.partner_id,
                    rp.status,
                    sr.code,
                    p.code,
                    p.business_name
                FROM request_partners rp
                JOIN service_requests sr
                    ON sr.id = rp.service_request_id
                JOIN partners p
                    ON p.id = rp.partner_id
                WHERE rp.id = %s
                """,
                (payload.request_partner_id,),
            )

            candidate = cur.fetchone()

            if not candidate:
                raise HTTPException(
                    status_code=404,
                    detail="Candidatura no encontrada.",
                )

            service_request_id = candidate[1]
            partner_id = candidate[2]
            cur.execute("SELECT id FROM partners WHERE id=%s FOR SHARE", (partner_id,))
            # Serializar cualquier respuesta, no solo la aceptación.
            cur.execute("SELECT status, assigned_partner_id FROM service_requests WHERE id = %s FOR UPDATE", (service_request_id,))
            request = cur.fetchone()
            from app.services.commercial_lifecycle import require_unexpired
            require_unexpired(cur, 'service_requests', service_request_id)
            require_unexpired(cur, 'request_partners', payload.request_partner_id)
            if not request or request[0] not in {"searching", "offers_received"} or request[1] is not None:
                raise HTTPException(409, "La solicitud ya no admite respuestas.")
            cur.execute("SELECT status,proposed_price,proposed_currency,updated_at FROM request_partners WHERE id = %s FOR UPDATE", (payload.request_partner_id,))
            current = cur.fetchone()
            if not current:
                raise HTTPException(404, "Candidatura no encontrada.")
            recheck_owner(cur, "candidate", payload.request_partner_id)
            if action != 'reject':
                require_bookable_candidate(cur, payload.request_partner_id)
            if expected_offer is not None:
                if (current[0] != 'counter_offered' or current[1] is None or current[2] is None
                        or current[3] != expected_offer):
                    raise HTTPException(409,'La contraoferta cambió o ya no admite aceptación.')
            candidate_status = current[0]
            request_code = candidate[4]
            partner_code = candidate[5]
            business_name = candidate[6]

            if candidate_status not in {
                "sent",
                "delivered",
                "viewed",
                "counter_offered",
            }:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Esta candidatura ya no puede "
                        "responder a la solicitud."
                    ),
                )

            # REJECT
            if action == "reject":
                cur.execute(
                    """
                    UPDATE request_partners
                    SET
                        status = 'rejected',
                        partner_message = %s,
                        responded_at = now(),
                        updated_at = now()
                    WHERE id = %s
                    RETURNING id
                    """,
                    (
                        payload.partner_message,
                        payload.request_partner_id,
                    ),
                )

                conn.commit()

                return {
                    "service_request": request_code,
                    "partner_code": partner_code,
                    "business_name": business_name,
                    "action": "reject",
                    "status": "rejected",
                    "winner": False,
                }

            # COUNTER OFFER
            if action == "counter_offer":
                cur.execute(
                    """
                    UPDATE request_partners
                    SET
                        status = 'counter_offered',
                        proposed_time = %s,
                        proposed_price = %s,
                        proposed_currency = %s,
                        partner_message = %s,
                        responded_at = now(),
                        updated_at = now()
                    WHERE id = %s
                    RETURNING id
                    """,
                    (
                        payload.proposed_time,
                        payload.proposed_price,
                        payload.proposed_currency,
                        payload.partner_message,
                        payload.request_partner_id,
                    ),
                )

                cur.execute(
                    """
                    UPDATE service_requests
                    SET
                        status = 'offers_received',
                        updated_at = now()
                    WHERE id = %s
                      AND status = 'searching'
                    """,
                    (service_request_id,),
                )

                conn.commit()

                return {
                    "service_request": request_code,
                    "partner_code": partner_code,
                    "business_name": business_name,
                    "action": "counter_offer",
                    "status": "counter_offered",
                    "winner": False,
                }

            # ACCEPT
            # Bloqueamos la solicitud para impedir dos ganadores.
            cur.execute(
                """
                SELECT
                    id,
                    status,
                    assigned_partner_id
                FROM service_requests
                WHERE id = %s
                FOR UPDATE
                """,
                (service_request_id,),
            )

            locked_request = cur.fetchone()

            if not locked_request:
                raise HTTPException(
                    status_code=404,
                    detail="Solicitud no encontrada.",
                )

            if locked_request[2] is not None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "La solicitud ya fue asignada "
                        "a otro partner."
                    ),
                )

            if locked_request[1] not in {
                "searching",
                "offers_received",
            }:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "La solicitud ya no está disponible "
                        "para aceptación."
                    ),
                )

            # Convertir este candidato en ganador.
            cur.execute(
                """
                UPDATE request_partners
                SET
                    status = 'accepted',
                    proposed_time = COALESCE(%s, proposed_time),
                    proposed_price = COALESCE(%s, proposed_price),
                    proposed_currency = COALESCE(%s, proposed_currency),
                    partner_message = COALESCE(%s, partner_message),
                    responded_at = now(),
                    accepted_at = now(),
                    is_winner = true,
                    updated_at = now()
                WHERE id = %s
                  AND status IN (
                      'sent',
                      'delivered',
                      'viewed',
                      'counter_offered'
                  )
                  AND is_winner = false
                RETURNING id
                """,
                (
                    payload.proposed_time,
                    payload.proposed_price,
                    payload.proposed_currency,
                    payload.partner_message,
                    payload.request_partner_id,
                ),
            )

            accepted = cur.fetchone()

            if not accepted:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "La candidatura ya no está "
                        "disponible para aceptación."
                    ),
                )

            # Asignar partner ganador.
            cur.execute(
                """
                UPDATE service_requests
                SET
                    assigned_partner_id = %s,
                    assigned_at = now(),
                    status = 'partner_assigned',
                    updated_at = now()
                WHERE id = %s
                  AND assigned_partner_id IS NULL
                  AND status IN (
                      'searching',
                      'offers_received'
                  )
                RETURNING id
                """,
                (
                    partner_id,
                    service_request_id,
                ),
            )

            assigned = cur.fetchone()

            if not assigned:
                raise HTTPException(
                    status_code=409,
                    detail="No fue posible asignar el partner.",
                )

            # Los demás candidatos pierden.
            cur.execute(
                """
                UPDATE request_partners
                SET
                    status = 'lost',
                    updated_at = now()
                WHERE service_request_id = %s
                  AND id <> %s
                  AND status IN (
                      'pending',
                      'sent',
                      'delivered',
                      'viewed',
                      'counter_offered'
                  )
                """,
                (
                    service_request_id,
                    payload.request_partner_id,
                ),
            )

        conn.commit()

    return {
        "service_request": request_code,
        "partner_code": partner_code,
        "business_name": business_name,
        "action": "accept",
        "status": "accepted",
        "winner": True,
    }
