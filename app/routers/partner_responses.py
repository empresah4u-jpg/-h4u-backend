from datetime import time
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import get_connection


router = APIRouter(
    prefix="/partner-responses",
    tags=["Partner Responses"],
)


class PartnerResponseCreate(BaseModel):
    request_partner_id: UUID
    action: str

    proposed_time: Optional[time] = None
    proposed_price: Optional[float] = Field(default=None, ge=0)
    proposed_currency: Optional[str] = None
    partner_message: Optional[str] = None


@router.post("", status_code=200)
def respond_to_request(payload: PartnerResponseCreate):

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
            candidate_status = candidate[3]
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