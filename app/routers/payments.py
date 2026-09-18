from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_connection


router = APIRouter(
    prefix="/payments",
    tags=["Payments"],
)


class PaymentCreate(BaseModel):
    reservation_code: str
    payment_method: str
    received_by: str
    payment_reference: Optional[str] = None
    external_reference: Optional[str] = None
    proof_url: Optional[str] = None


@router.post("", status_code=201)
def create_payment(payload: PaymentCreate):

    payment_method = payload.payment_method.strip().lower()
    received_by = payload.received_by.strip().lower()

    valid_methods = {
        "yape",
        "plin",
        "bank_transfer",
        "card",
        "mercado_pago",
        "izipay",
        "cash",
        "other",
    }

    valid_receivers = {
        "partner",
        "h4u",
        "processor",
    }

    if payment_method not in valid_methods:
        raise HTTPException(
            status_code=400,
            detail="Método de pago no válido.",
        )

    if received_by not in valid_receivers:
        raise HTTPException(
            status_code=400,
            detail="received_by no válido.",
        )

    with get_connection() as conn:
        with conn.cursor() as cur:

            # 1. Bloquear la reserva.
            cur.execute(
                """
                SELECT
                    id,
                    code,
                    agreed_price,
                    currency,
                    status
                FROM reservations
                WHERE code = %s
                FOR UPDATE
                """,
                (payload.reservation_code,),
            )

            reservation = cur.fetchone()

            if not reservation:
                raise HTTPException(
                    status_code=404,
                    detail="Reserva no encontrada.",
                )

            reservation_id = reservation[0]
            reservation_code = reservation[1]
            agreed_price = reservation[2]
            currency = reservation[3]
            reservation_status = reservation[4]

            if reservation_status != "payment_pending":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "La reserva no está pendiente "
                        "de pago."
                    ),
                )

            if agreed_price is None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "La reserva no tiene un precio "
                        "acordado."
                    ),
                )

            # 2. Evitar crear otro pago activo
            # para la misma reserva.
            cur.execute(
                """
                SELECT
                    code,
                    status
                FROM payments
                WHERE reservation_id = %s
                  AND status IN (
                      'pending',
                      'reported',
                      'waiting_verification',
                      'paid'
                  )
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (reservation_id,),
            )

            existing_payment = cur.fetchone()

            if existing_payment:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "La reserva ya tiene un pago activo: "
                        f"{existing_payment[0]} "
                        f"({existing_payment[1]})."
                    ),
                )

            # 3. Definir estado inicial.
            #
            # Efectivo:
            # todavía debe ser confirmado por las partes.
            #
            # Yape / Plin / transferencia:
            # el usuario reporta el pago, pero H4U
            # todavía debe verificarlo.
            #
            # Procesadores:
            # más adelante pasarán a paid mediante
            # webhook verificado.
            if payment_method == "cash":
                initial_status = "pending"
                reported_at_sql = "NULL"

            elif payment_method in {
                "yape",
                "plin",
                "bank_transfer",
            }:
                initial_status = "waiting_verification"
                reported_at_sql = "now()"

            else:
                initial_status = "pending"
                reported_at_sql = "NULL"

            # 4. Generar código de pago.
            cur.execute(
                """
                SELECT
                    'PAY-' ||
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

            payment_code = cur.fetchone()[0]

            # 5. Crear pago.
            cur.execute(
                f"""
                INSERT INTO payments (
                    code,
                    reservation_id,
                    amount,
                    currency,
                    payment_method,
                    received_by,
                    status,
                    external_reference,
                    payment_reference,
                    proof_url,
                    reported_at
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
                    {reported_at_sql}
                )
                RETURNING
                    id,
                    code,
                    status,
                    created_at
                """,
                (
                    payment_code,
                    reservation_id,
                    agreed_price,
                    currency,
                    payment_method,
                    received_by,
                    initial_status,
                    payload.external_reference,
                    payload.payment_reference,
                    payload.proof_url,
                ),
            )

            created = cur.fetchone()

        conn.commit()

    return {
        "id": str(created[0]),
        "code": created[1],
        "status": created[2],
        "created_at": created[3],
        "reservation_code": reservation_code,
        "amount": float(agreed_price),
        "currency": currency.strip(),
        "payment_method": payment_method,
        "received_by": received_by,
        "verified": False,
        "paid": False,
    }


@router.post("/{payment_code}/confirm-partner", status_code=200)
def confirm_cash_by_partner(payment_code: str):

    with get_connection() as conn:
        with conn.cursor() as cur:

            # 1. Bloquear el pago.
            cur.execute(
                """
                SELECT
                    id,
                    code,
                    payment_method,
                    received_by,
                    status,
                    partner_confirmed_at,
                    customer_confirmed_at
                FROM payments
                WHERE code = %s
                FOR UPDATE
                """,
                (payment_code,),
            )

            payment = cur.fetchone()

            if not payment:
                raise HTTPException(
                    status_code=404,
                    detail="Pago no encontrado.",
                )

            payment_id = payment[0]
            payment_method = payment[2]
            received_by = payment[3]
            status = payment[4]
            partner_confirmed_at = payment[5]

            # 2. Solo aplica a efectivo.
            if payment_method != "cash":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Este endpoint solo permite "
                        "confirmar pagos en efectivo."
                    ),
                )

            # 3. El efectivo debe haber sido
            # recibido directamente por el partner.
            if received_by != "partner":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Este pago no está configurado "
                        "para ser recibido por el partner."
                    ),
                )

            # 4. Debe seguir pendiente.
            if status != "pending":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "El pago ya no está pendiente "
                        "de confirmación."
                    ),
                )

            if partner_confirmed_at is not None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "El partner ya confirmó "
                        "este pago."
                    ),
                )

            # 5. Registrar confirmación del partner.
            #
            # IMPORTANTE:
            # esto todavía NO significa que el pago
            # esté completamente validado.
            cur.execute(
                """
                UPDATE payments
                SET
                    partner_confirmed_at = now(),
                    status = 'waiting_verification',
                    updated_at = now()
                WHERE id = %s
                  AND status = 'pending'
                  AND partner_confirmed_at IS NULL
                RETURNING
                    code,
                    status,
                    partner_confirmed_at
                """,
                (payment_id,),
            )

            updated = cur.fetchone()

            if not updated:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "No fue posible confirmar "
                        "el pago."
                    ),
                )

        conn.commit()

    return {
        "code": updated[0],
        "status": updated[1],
        "partner_confirmed": True,
        "partner_confirmed_at": updated[2],
        "customer_confirmed": False,
        "paid": False,
    }

@router.post("/{payment_code}/confirm-customer", status_code=200)
def confirm_cash_by_customer(payment_code: str):

    with get_connection() as conn:
        with conn.cursor() as cur:

            # 1. Bloquear el pago.
            cur.execute(
                """
                SELECT
                    p.id,
                    p.code,
                    p.reservation_id,
                    p.payment_method,
                    p.received_by,
                    p.status,
                    p.partner_confirmed_at,
                    p.customer_confirmed_at,
                    r.code
                FROM payments p
                JOIN reservations r
                    ON r.id = p.reservation_id
                WHERE p.code = %s
                FOR UPDATE OF p, r
                """,
                (payment_code,),
            )

            payment = cur.fetchone()

            if not payment:
                raise HTTPException(
                    status_code=404,
                    detail="Pago no encontrado.",
                )

            payment_id = payment[0]
            reservation_id = payment[2]
            payment_method = payment[3]
            received_by = payment[4]
            status = payment[5]
            partner_confirmed_at = payment[6]
            customer_confirmed_at = payment[7]
            reservation_code = payment[8]

            # 2. Esta confirmación aplica al flujo
            # de efectivo recibido por el partner.
            if payment_method != "cash":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Este endpoint solo permite "
                        "confirmar pagos en efectivo."
                    ),
                )

            if received_by != "partner":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Este pago no fue configurado "
                        "para ser recibido por el partner."
                    ),
                )

            # 3. Evitar confirmaciones repetidas.
            if customer_confirmed_at is not None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "El turista ya confirmó "
                        "este pago."
                    ),
                )

            if status == "paid":
                raise HTTPException(
                    status_code=409,
                    detail="El pago ya está pagado.",
                )

            if status not in {
                "pending",
                "waiting_verification",
            }:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "El pago no se encuentra en un "
                        "estado válido para confirmación."
                    ),
                )

            # 4. Si el partner todavía no confirmó,
            # registrar solamente la confirmación
            # del turista.
            if partner_confirmed_at is None:

                cur.execute(
                    """
                    UPDATE payments
                    SET
                        customer_confirmed_at = now(),
                        status = 'waiting_verification',
                        updated_at = now()
                    WHERE id = %s
                      AND customer_confirmed_at IS NULL
                    RETURNING
                        code,
                        status,
                        customer_confirmed_at
                    """,
                    (payment_id,),
                )

                updated = cur.fetchone()

                if not updated:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "No fue posible registrar "
                            "la confirmación del turista."
                        ),
                    )

                conn.commit()

                return {
                    "code": updated[0],
                    "status": updated[1],
                    "reservation_code": reservation_code,
                    "partner_confirmed": False,
                    "customer_confirmed": True,
                    "customer_confirmed_at": updated[2],
                    "verified": False,
                    "paid": False,
                }

            # 5. El partner ya confirmó.
            # Registrar confirmación del turista
            # y completar el pago.
            cur.execute(
                """
                UPDATE payments
                SET
                    customer_confirmed_at = now(),
                    status = 'paid',
                    verified_at = now(),
                    paid_at = now(),
                    updated_at = now()
                WHERE id = %s
                  AND customer_confirmed_at IS NULL
                  AND partner_confirmed_at IS NOT NULL
                  AND status = 'waiting_verification'
                RETURNING
                    code,
                    status,
                    customer_confirmed_at,
                    verified_at,
                    paid_at
                """,
                (payment_id,),
            )

            updated = cur.fetchone()

            if not updated:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "No fue posible completar "
                        "la confirmación del pago."
                    ),
                )

            # 6. Confirmar también la reserva.
            cur.execute(
                """
                UPDATE reservations
                SET
                    status = 'confirmed',
                    confirmed_at = COALESCE(
                        confirmed_at,
                        now()
                    ),
                    updated_at = now()
                WHERE id = %s
                  AND status = 'payment_pending'
                RETURNING
                    code,
                    status,
                    confirmed_at
                """,
                (reservation_id,),
            )

            reservation = cur.fetchone()

            if not reservation:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "El pago fue confirmado, pero "
                        "la reserva no estaba en un "
                        "estado válido para confirmarse."
                    ),
                )

        conn.commit()

    return {
        "code": updated[0],
        "status": updated[1],
        "reservation_code": reservation[0],
        "reservation_status": reservation[1],
        "partner_confirmed": True,
        "customer_confirmed": True,
        "customer_confirmed_at": updated[2],
        "verified_at": updated[3],
        "paid_at": updated[4],
        "verified": True,
        "paid": True,
    }