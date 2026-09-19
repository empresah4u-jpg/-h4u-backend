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


class ReservationCancel(BaseModel):
    reason: str


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

            # 6. Determinar hora acordada.
            service_time = (
                proposed_time
                if proposed_time is not None
                else preferred_time
            )

            # 7. Determinar precio.
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


@router.post("/{reservation_code}/cancel", status_code=200)
def cancel_reservation(
    reservation_code: str,
    payload: ReservationCancel,
):

    cancellation_reason = payload.reason.strip()

    if not cancellation_reason:
        raise HTTPException(
            status_code=400,
            detail="Debe indicar el motivo de cancelación.",
        )

    with get_connection() as conn:
        with conn.cursor() as cur:

            # 1. Bloquear la reserva para evitar
            # cancelaciones concurrentes.
            cur.execute(
                """
                SELECT
                    id,
                    code,
                    status,
                    cancelled_at,
                    cancellation_reason
                FROM reservations
                WHERE code = %s
                FOR UPDATE
                """,
                (reservation_code,),
            )

            reservation = cur.fetchone()

            if not reservation:
                raise HTTPException(
                    status_code=404,
                    detail="Reserva no encontrada.",
                )

            reservation_id = reservation[0]
            current_status = reservation[2]

            # 2. Evitar una segunda cancelación.
            if current_status == "cancelled":
                raise HTTPException(
                    status_code=409,
                    detail="La reserva ya está cancelada.",
                )

            # 3. Estados finales que no admiten cancelación.
            if current_status in {
                "completed",
                "no_show",
            }:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Una reserva en estado "
                        f"{current_status} no puede cancelarse."
                    ),
                )

            # 4. Solo estos estados pueden cancelarse.
            cancellable_statuses = {
                "pending",
                "awaiting_passenger_data",
                "payment_pending",
                "confirmed",
                "ready",
            }

            if current_status not in cancellable_statuses:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "La reserva no se encuentra en un "
                        "estado que permita cancelación."
                    ),
                )

            # 5. Si existe dinero ya cobrado o un pago
            # en disputa, no cancelar directamente.
            # Debe resolverse mediante el flujo financiero
            # correspondiente.
            cur.execute(
                """
                SELECT
                    code,
                    status
                FROM payments
                WHERE reservation_id = %s
                  AND status IN (
                      'paid',
                      'disputed',
                      'partially_refunded'
                  )
                LIMIT 1
                FOR UPDATE
                """,
                (reservation_id,),
            )

            protected_payment = cur.fetchone()

            if protected_payment:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "La reserva tiene un pago que requiere "
                        "revisión o reembolso antes de cancelarse."
                    ),
                )

            # 6. Cancelar pagos que todavía no han sido
            # completados.
            #
            # Esto evita que un pago pendiente continúe
            # activo después de cancelar la reserva.
            cur.execute(
                """
                UPDATE payments
                SET
                    status = 'cancelled',
                    updated_at = now()
                WHERE reservation_id = %s
                  AND status IN (
                      'pending',
                      'reported',
                      'waiting_verification'
                  )
                """,
                (reservation_id,),
            )

            cancelled_payments = cur.rowcount

            # 7. Cancelar la reserva conservando
            # trazabilidad.
            cur.execute(
                """
                UPDATE reservations
                SET
                    status = 'cancelled',
                    cancelled_at = now(),
                    cancellation_reason = %s,
                    updated_at = now()
                WHERE id = %s
                  AND status = %s
                RETURNING
                    code,
                    status,
                    cancelled_at,
                    cancellation_reason
                """,
                (
                    cancellation_reason,
                    reservation_id,
                    current_status,
                ),
            )

            cancelled = cur.fetchone()

            if not cancelled:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "No fue posible cancelar la reserva."
                    ),
                )

        conn.commit()

    return {
        "reservation_code": cancelled[0],
        "previous_status": current_status,
        "status": cancelled[1],
        "cancelled_at": cancelled[2],
        "cancellation_reason": cancelled[3],
        "cancelled_payments": cancelled_payments,
    }