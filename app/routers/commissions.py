from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, HTTPException

from app.db import get_connection


router = APIRouter(
    prefix="/commissions",
    tags=["Commissions"],
)


@router.post("/from-payment/{payment_code}", status_code=201)
def create_commission_from_payment(payment_code: str):

    with get_connection() as conn:
        with conn.cursor() as cur:

            # 1. Obtener y bloquear el pago.
            #
            # La comisión solo puede generarse cuando
            # el pago ya fue confirmado.
            cur.execute(
                """
                SELECT
                    p.id,
                    p.code,
                    p.reservation_id,
                    p.amount,
                    p.currency,
                    p.status
                FROM payments p
                WHERE p.code = %s
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
            reservation_id = payment[2]
            payment_amount = payment[3]
            payment_currency = payment[4].strip()
            payment_status = payment[5]

            if payment_status != "paid":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "La comisión solo puede generarse "
                        "desde un pago confirmado."
                    ),
                )

            # 2. Comprobar idempotencia.
            #
            # Además de esta validación, PostgreSQL
            # tiene uq_commissions_payment.
            cur.execute(
                """
                SELECT
                    code,
                    commission_amount,
                    currency,
                    status
                FROM commissions
                WHERE payment_id = %s
                LIMIT 1
                """,
                (payment_id,),
            )

            existing = cur.fetchone()

            if existing:
                return {
                    "code": existing[0],
                    "commission_amount": float(existing[1]),
                    "currency": existing[2].strip(),
                    "status": existing[3],
                    "already_existed": True,
                }

            # 3. Obtener la reserva y el vínculo
            # comercial exacto que ganó la solicitud.
            cur.execute(
                """
                SELECT
                    r.partner_id,
                    r.product_id,
                    r.request_partner_id,
                    pp.commission_type,
                    pp.commission_value,
                    pa.commission_type,
                    pa.commission_value
                FROM reservations r
                LEFT JOIN request_partners rp
                    ON rp.id = r.request_partner_id
                LEFT JOIN product_partners pp
                    ON pp.id = rp.product_partner_id
                   AND pp.product_id = r.product_id
                   AND pp.partner_id = r.partner_id
                JOIN partners pa
                    ON pa.id = r.partner_id
                WHERE r.id = %s
                """,
                (reservation_id,),
            )

            commercial = cur.fetchone()

            if not commercial:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "No se encontró la configuración "
                        "comercial de la reserva."
                    ),
                )

            partner_id = commercial[0]
            product_id = commercial[1]
            request_partner_id = commercial[2]

            product_commission_type = commercial[3]
            product_commission_value = commercial[4]

            partner_commission_type = commercial[5]
            partner_commission_value = commercial[6]

            if partner_id is None or product_id is None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "La reserva no tiene partner "
                        "o producto asociado."
                    ),
                )

            if request_partner_id is None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "La reserva no tiene una "
                        "asignación comercial asociada."
                    ),
                )

            # 4. Prioridad:
            #
            # product_partners override
            #       ↓
            # configuración general del partner
            if (
                product_commission_type is not None
                and product_commission_value is not None
            ):
                commission_type = product_commission_type
                commission_value = product_commission_value

            elif (
                partner_commission_type is not None
                and partner_commission_value is not None
            ):
                commission_type = partner_commission_type
                commission_value = partner_commission_value

            else:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "No existe una comisión configurada "
                        "para esta operación."
                    ),
                )

            # 5. Calcular comisión usando Decimal.
            base_amount = Decimal(payment_amount)
            commission_value = Decimal(commission_value)

            if commission_type == "percentage":

                commission_amount = (
                    base_amount
                    * commission_value
                    / Decimal("100")
                )

                commission_rate = commission_value

            elif commission_type == "fixed":

                commission_amount = commission_value
                commission_rate = None

            else:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Tipo de comisión no soportado "
                        "para esta operación."
                    ),
                )

            commission_amount = commission_amount.quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )

            # 6. Generar código.
            cur.execute(
                """
                SELECT
                    'COMM-' ||
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

            commission_code = cur.fetchone()[0]

            # 7. Registrar la comisión como earned.
            #
            # El dinero ya fue confirmado, por lo que
            # H4U ya tiene una cuenta por cobrar
            # al partner.
            cur.execute(
                """
                INSERT INTO commissions (
                    code,
                    reservation_id,
                    partner_id,
                    payment_id,
                    base_amount,
                    commission_type,
                    commission_rate,
                    commission_amount,
                    currency,
                    trigger_event,
                    status,
                    earned_at
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
                    'payment_confirmed',
                    'earned',
                    now()
                )
                RETURNING
                    id,
                    code,
                    base_amount,
                    commission_type,
                    commission_rate,
                    commission_amount,
                    currency,
                    trigger_event,
                    status,
                    earned_at
                """,
                (
                    commission_code,
                    reservation_id,
                    partner_id,
                    payment_id,
                    base_amount,
                    commission_type,
                    commission_rate,
                    commission_amount,
                    payment_currency,
                ),
            )

            created = cur.fetchone()

        conn.commit()

    return {
        "id": str(created[0]),
        "code": created[1],
        "base_amount": float(created[2]),
        "commission_type": created[3],
        "commission_rate": (
            float(created[4])
            if created[4] is not None
            else None
        ),
        "commission_amount": float(created[5]),
        "currency": created[6].strip(),
        "trigger_event": created[7],
        "status": created[8],
        "earned_at": created[9],
        "payment_code": payment_code,
        "already_existed": False,
    }