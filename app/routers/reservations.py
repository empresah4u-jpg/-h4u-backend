from typing import Optional
from decimal import Decimal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import get_connection
from app.auth import authorize, recheck_owner, prelock_partner
from app.services.commercial_lifecycle import require_unexpired, slot_for_reservation
from app.services.commercial_eligibility import require_bookable_candidate


router = APIRouter(
    prefix="/reservations",
    tags=["Reservations"],
)


class ReservationCreate(BaseModel):
    service_request_code: str


class ReservationCancel(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)
    idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=200)


@router.post("", status_code=201, dependencies=[authorize("reservation.create")])
def create_reservation(payload: ReservationCreate):

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT assigned_partner_id FROM service_requests WHERE code=%s", (payload.service_request_code,))
            owner = cur.fetchone()
            if owner and owner[0]:
                cur.execute("SELECT id FROM partners WHERE id=%s FOR UPDATE", (owner[0],))
            prelock_partner(cur, "request", payload.service_request_code)

            # 1. Bloquear la solicitud durante la creación.
            cur.execute(
                """
                SELECT
                    sr.id,
                    sr.code,
                    sr.status,
                    sr.traveler_id,
                    sr.product_id,
                    sr.service_date,
                    sr.preferred_time,
                    sr.passenger_count,
                    sr.assigned_partner_id
                FROM service_requests sr
                WHERE sr.code = %s
                FOR UPDATE
                """,
                (payload.service_request_code,),
            )

            request = cur.fetchone()

            if not request:
                raise HTTPException(
                    status_code=404,
                    detail="Solicitud no encontrada.",
                )

            recheck_owner(cur, "request", payload.service_request_code)
            require_unexpired(cur, "service_requests", request[0])
            service_request_id = request[0]
            request_code = request[1]
            request_status = request[2]
            traveler_id = request[3]
            product_id = request[4]
            service_date = request[5]
            preferred_time = request[6]
            passenger_count = request[7]
            assigned_partner_id = request[8]

            # 2. Solo una solicitud asignada puede convertirse
            # en reserva.
            if request_status != "partner_assigned":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "La solicitud todavía no tiene "
                        "un partner asignado."
                    ),
                )

            if assigned_partner_id is None:
                raise HTTPException(
                    status_code=409,
                    detail="La solicitud no tiene partner ganador.",
                )

            # 3. Revalidar el producto antes de reservar.
            # Una solicitud antigua no puede saltarse las reglas
            # comerciales actuales del producto.
            cur.execute(
                """
                SELECT
                    status,
                    reservations_enabled
                FROM products
                WHERE id = %s
                """,
                (product_id,),
            )

            product = cur.fetchone()

            if not product:
                raise HTTPException(
                    status_code=404,
                    detail="Producto no encontrado.",
                )

            product_status = product[0]
            reservations_enabled = product[1]

            if product_status != "active":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "El producto no está activo "
                        "y no puede reservarse."
                    ),
                )

            if not reservations_enabled:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Las reservas están deshabilitadas "
                        "para este producto."
                    ),
                )

            # 4. Evitar una segunda reserva.
            cur.execute(
                """
                SELECT
                    id,
                    code,
                    status
                FROM reservations
                WHERE service_request_id = %s
                """,
                (service_request_id,),
            )

            existing = cur.fetchone()

            if existing:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Esta solicitud ya tiene una reserva: "
                        f"{existing[1]}."
                    ),
                )

            # 5. Obtener el request_partner ganador y
            # la configuración comercial.
            cur.execute(
                """
                SELECT
                    rp.id,
                    rp.partner_id,
                    rp.status
                FROM request_partners rp
                WHERE rp.service_request_id = %s
                  AND rp.is_winner = true
                """,
                (service_request_id,),
            )

            winner = cur.fetchone()

            if not winner:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "No se encontró la candidatura "
                        "ganadora."
                    ),
                )

            request_partner_id = winner[0]
            require_bookable_candidate(cur, request_partner_id)
            winner_partner_id = winner[1]
            winner_status = winner[2]
            if winner_status != "accepted":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "La candidatura ganadora no está "
                        "aceptada."
                    ),
                )

            if winner_partner_id != assigned_partner_id:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "El partner ganador no coincide con "
                        "el partner asignado."
                    ),
                )

            # New reservations must use an immutable, explicitly accepted snapshot.
            cur.execute("""SELECT id,amount_total,currency,service_date,service_time,passenger_count,
                traveler_id,product_id,partner_id FROM request_offer_consents
                WHERE service_request_id=%s AND request_partner_id=%s""",(service_request_id,request_partner_id))
            consent=cur.fetchone()
            if consent is None:
                raise HTTPException(409, "Nueva reserva requiere consentimiento explícito del turista.")
            consent_id,agreed_price,currency,service_date,service_time,passenger_count,ct,cp,partner=consent
            if (ct,cp,partner)!=(traveler_id,product_id,assigned_partner_id):
                raise HTTPException(409, "La reserva no coincide con el consentimiento.")
            slot_id = slot_for_reservation(cur, product_id, assigned_partner_id, service_date, service_time, passenger_count)

            if not Decimal(agreed_price).is_finite() or agreed_price < 0:
                raise HTTPException(409, "El precio acordado no es válido.")
            if not currency or len(currency.strip()) != 3 or not currency.strip().isascii() or not currency.strip().isalpha():
                raise HTTPException(409, "La moneda acordada no es válida.")

            # 8. Generar código.
            cur.execute(
                """
                SELECT
                    'RES-' ||
                    upper(
                        substr(
                            replace(
                                gen_random_uuid()::text,
                                '-',
                                ''
                            ),
                            1,
                            12
                        )
                    )
                """
            )

            reservation_code = cur.fetchone()[0]

            # 9. Crear reserva.
            cur.execute(
                """
                INSERT INTO reservations (
                    code,
                    service_request_id,
                    traveler_id,
                    product_id,
                    partner_id,
                    request_partner_id,
                    service_date,
                    service_time,
                    passenger_count,
                    agreed_price,
                    currency,
                    status, slot_id, consent_id
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    'awaiting_passenger_data', %s, %s
                )
                RETURNING
                    id,
                    code,
                    status,
                    created_at
                """,
                (
                    reservation_code,
                    service_request_id,
                    traveler_id,
                    product_id,
                    assigned_partner_id,
                    request_partner_id,
                    service_date,
                    service_time,
                    passenger_count,
                    agreed_price,
                    currency,
                    slot_id,
                    consent_id,
                ),
            )

            created = cur.fetchone()

        conn.commit()

    return {
        "id": str(created[0]),
        "code": created[1],
        "status": created[2],
        "created_at": created[3],
        "service_request": request_code,
        "service_date": service_date,
        "service_time": service_time,
        "passenger_count": passenger_count,
        "agreed_price": float(agreed_price),
        "currency": currency.strip(),
        "partner_id": str(assigned_partner_id),
        "request_partner_id": str(request_partner_id),
    }


@router.post("/{reservation_code}/cancel", status_code=200, dependencies=[authorize("reservation.cancel")])
def cancel_reservation(
    reservation_code: str,
    payload: ReservationCancel,
):

    from app.services.commercial_lifecycle import cancel_reservation as cancel
    # Legacy callers receive a stable operation key, never a public bypass of policy.
    with get_connection() as conn:
        with conn.cursor() as cur:
            result = cancel(cur, reservation_code, payload.reason,
                            payload.idempotency_key or 'legacy-cancel')
        conn.commit()
    return result
