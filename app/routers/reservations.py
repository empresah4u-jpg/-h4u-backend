from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_connection


router = APIRouter(
    prefix="/reservations",
    tags=["Reservations"],
)


class ReservationCreate(BaseModel):
    service_request_code: str


@router.post("", status_code=201)
def create_reservation(payload: ReservationCreate):

    with get_connection() as conn:
        with conn.cursor() as cur:

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

            # 3. Evitar una segunda reserva.
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

            # 4. Obtener el request_partner ganador y
            # la configuración comercial.
            cur.execute(
                """
                SELECT
                    rp.id,
                    rp.partner_id,
                    rp.status,
                    rp.proposed_time,
                    rp.proposed_price,
                    rp.proposed_currency,
                    pp.partner_price,
                    pp.currency
                FROM request_partners rp
                LEFT JOIN product_partners pp
                    ON pp.id = rp.product_partner_id
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
            winner_partner_id = winner[1]
            winner_status = winner[2]
            proposed_time = winner[3]
            proposed_price = winner[4]
            proposed_currency = winner[5]
            partner_price = winner[6]
            partner_currency = winner[7]

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

            # 5. Determinar hora acordada.
            service_time = (
                proposed_time
                if proposed_time is not None
                else preferred_time
            )

            # 6. Determinar precio.
            #
            # Si existe proposed_price, se considera precio total
            # de la contraoferta.
            #
            # Si no existe, partner_price se considera precio
            # unitario por pasajero.
            agreed_price: Optional[object] = None
            currency = None

            if proposed_price is not None:
                agreed_price = proposed_price
                currency = (
                    proposed_currency
                    or partner_currency
                    or "PEN"
                )

            elif partner_price is not None:
                agreed_price = (
                    partner_price * passenger_count
                )
                currency = partner_currency or "PEN"

            else:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "No existe un precio configurado "
                        "para crear la reserva."
                    ),
                )

            # 7. Generar código.
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

            # 8. Crear reserva.
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
                    status
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
                    'awaiting_passenger_data'
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