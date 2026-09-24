from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_connection
from app.auth import authorize, recheck_owner, audit_actor
from app.services.finance import (allocate_credits, lock_settlement_partner,
                                  validate_settlement_balance, snapshot_settlement)


router = APIRouter(
    prefix="/settlements",
    tags=["Settlements"],
)


class SettlementCreate(BaseModel):
    partner_code: str
    period_start: date
    period_end: date
    currency: str
    due_date: date


@router.post("", status_code=201, dependencies=[authorize("settlement.create")])
def create_settlement(payload: SettlementCreate):

    partner_code = payload.partner_code.strip()
    currency = payload.currency.strip().upper()

    # 1. Validaciones básicas.
    if payload.period_end < payload.period_start:
        raise HTTPException(
            status_code=400,
            detail=(
                "period_end no puede ser anterior "
                "a period_start."
            ),
        )

    if payload.due_date < payload.period_end:
        raise HTTPException(
            status_code=400,
            detail=(
                "due_date no puede ser anterior "
                "al cierre del período."
            ),
        )

    if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
        raise HTTPException(
            status_code=400,
            detail="La moneda debe usar código ISO de 3 letras.",
        )

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('h4u.partner_actor',%s,true)", (audit_actor()[0],))

            # 2. Obtener y bloquear el partner.
            cur.execute(
                """
                SELECT
                    id,
                    code,
                    business_name,
                    status
                FROM partners
                WHERE code = %s
                FOR UPDATE
                """,
                (partner_code,),
            )

            partner = cur.fetchone()

            if not partner:
                raise HTTPException(
                    status_code=404,
                    detail="Partner no encontrado.",
                )

            partner_id = partner[0]
            business_name = partner[2]

            # No exigimos status=active aquí.
            # Un partner suspendido también puede
            # tener deuda pendiente que liquidar.

            # 3. Comprobar si el settlement del
            # período ya existe.
            cur.execute(
                """
                SELECT
                    id,
                    code,
                    total_commission_amount,
                    currency,
                    due_date,
                    status
                FROM partner_settlements
                WHERE partner_id = %s
                  AND period_start = %s
                  AND period_end = %s
                  AND currency = %s
                LIMIT 1
                """,
                (
                    partner_id,
                    payload.period_start,
                    payload.period_end,
                    currency,
                ),
            )

            existing = cur.fetchone()

            if existing:
                return {
                    "id": str(existing[0]),
                    "code": existing[1],
                    "partner_code": partner_code,
                    "business_name": business_name,
                    "period_start": payload.period_start,
                    "period_end": payload.period_end,
                    "total_commission_amount": float(existing[2]),
                    "currency": existing[3].strip(),
                    "due_date": existing[4],
                    "status": existing[5],
                    "already_existed": True,
                }

            # 4. Seleccionar comisiones pendientes.
            #
            # Reglas:
            # - deben estar earned
            # - deben ser del mismo partner
            # - misma moneda
            # - no pueden pertenecer ya a otro settlement
            # - earned_at debe ser anterior al final
            #   del período solicitado
            #
            # Esto permite arrastrar deuda de
            # períodos anteriores.
            cur.execute(
                """
                SELECT
                    c.id,
                    c.code,
                    c.commission_amount,
                    c.earned_at
                FROM commissions c
                LEFT JOIN settlement_commissions sc
                    ON sc.commission_id = c.id
                WHERE c.partner_id = %s
                  AND c.status = 'earned'
                  AND c.currency = %s
                  AND sc.commission_id IS NULL
                  AND c.earned_at < (%s::date + INTERVAL '1 day')
                ORDER BY
                    c.earned_at,
                    c.created_at,
                    c.id
                FOR UPDATE OF c
                """,
                (
                    partner_id,
                    currency,
                    payload.period_end,
                ),
            )

            commissions = cur.fetchall()

            if not commissions:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "No existen comisiones pendientes "
                        "para este partner, moneda y período."
                    ),
                )

            # 5. Calcular total.
            total_amount = sum(
                (
                    Decimal(row[2])
                    for row in commissions
                ),
                Decimal("0.00"),
            )

            total_amount = total_amount.quantize(
                Decimal("0.01")
            )

            # 6. Generar código.
            cur.execute(
                """
                SELECT
                    'SET-' ||
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

            settlement_code = cur.fetchone()[0]

            # 7. Crear settlement.
            #
            # pending_payment significa que el cierre
            # ya fue calculado y el partner tiene
            # una obligación pendiente con H4U.
            cur.execute(
                """
                INSERT INTO partner_settlements (
                    code,
                    partner_id,
                    period_start,
                    period_end,
                    total_commission_amount,
                    currency,
                    due_date,
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
                    'pending_payment'
                )
                RETURNING
                    id,
                    code,
                    total_commission_amount,
                    currency,
                    due_date,
                    status,
                    created_at
                """,
                (
                    settlement_code,
                    partner_id,
                    payload.period_start,
                    payload.period_end,
                    total_amount,
                    currency,
                    payload.due_date,
                ),
            )

            settlement = cur.fetchone()
            settlement_id = settlement[0]

            # 8. Vincular las comisiones al cierre.
            for commission in commissions:

                commission_id = commission[0]
                commission_amount = commission[2]

                cur.execute(
                    """
                    INSERT INTO settlement_commissions (
                        settlement_id,
                        commission_id,
                        commission_amount
                    )
                    VALUES (
                        %s,
                        %s,
                        %s
                    )
                    """,
                    (
                        settlement_id,
                        commission_id,
                        commission_amount,
                    ),
                )

            net_amount = allocate_credits(cur, partner_id, currency, settlement_id, total_amount)
            validate_settlement_balance(cur, settlement_id)

        conn.commit()

    return {
        "id": str(settlement[0]),
        "code": settlement[1],
        "partner_code": partner_code,
        "business_name": business_name,
        "period_start": payload.period_start,
        "period_end": payload.period_end,
        "commission_count": len(commissions),
        "total_commission_amount": float(net_amount),
        "gross_commission_amount": float(total_amount),
        "credit_amount": float(total_amount - net_amount),
        "currency": settlement[3].strip(),
        "due_date": settlement[4],
        "status": settlement[5],
        "created_at": settlement[6],
        "already_existed": False,
    }

class SettlementPaymentReport(BaseModel):
    payment_method: str
    payment_reference: Optional[str] = None
    proof_url: Optional[str] = None


@router.post("/{settlement_code}/report-payment", status_code=200, dependencies=[authorize("settlement.report")])
def report_settlement_payment(
    settlement_code: str,
    payload: SettlementPaymentReport,
):

    payment_method = payload.payment_method.strip().lower()

    valid_methods = {
        "yape",
        "plin",
        "bank_transfer",
        "cash",
        "other",
    }

    if payment_method not in valid_methods:
        raise HTTPException(
            status_code=400,
            detail="Método de pago no válido.",
        )

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('h4u.partner_actor',%s,true)", (audit_actor()[0],))
            lock_settlement_partner(cur, settlement_code)

            # 1. Bloquear el settlement.
            cur.execute(
                """
                SELECT
                    ps.id,
                    ps.code,
                    ps.total_commission_amount,
                    ps.currency,
                    ps.status,
                    ps.reported_at,
                    p.code,
                    p.business_name,
                    ps.reported_amount
                FROM partner_settlements ps
                JOIN partners p
                    ON p.id = ps.partner_id
                WHERE ps.code = %s
                FOR UPDATE OF ps
                """,
                (settlement_code,),
            )

            settlement = cur.fetchone()

            if not settlement:
                raise HTTPException(
                    status_code=404,
                    detail="Settlement no encontrado.",
                )

            recheck_owner(cur, "settlement", settlement_code)
            settlement_id = settlement[0]
            status = settlement[4]
            reported_at = settlement[5]
            partner_code = settlement[6]
            business_name = settlement[7]

            # 2. Un cierre pagado ya no se modifica.
            if status == "paid":
                raise HTTPException(
                    status_code=409,
                    detail="El settlement ya está pagado.",
                )

            if status in {
                "cancelled",
                "disputed",
            }:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "El settlement no se encuentra "
                        "en un estado válido para reportar pago."
                    ),
                )

            # 3. Evitar reportes repetidos.
            if reported_at is not None and settlement[8] in (None, settlement[2]):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "El partner ya reportó el pago "
                        "de este settlement."
                    ),
                )

            if status not in {
                "open",
                "pending_payment",
                "overdue",
                "waiting_verification",
            }:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "El settlement no puede reportar "
                        "un pago desde su estado actual."
                    ),
                )

            validate_settlement_balance(cur, settlement_id)
            snapshot_settlement(cur, settlement_id, "before_payment_report")

            # 4. Registrar el reporte.
            #
            # Reportar el pago NO significa todavía
            # que H4U haya recibido/verificado el dinero.
            cur.execute(
                """
                UPDATE partner_settlements
                SET
                    payment_method = %s,
                    payment_reference = %s,
                    proof_url = %s,
                    reported_at = now(),
                    reported_amount = total_commission_amount,
                    status = 'waiting_verification',
                    updated_at = now()
                WHERE id = %s
                  AND (reported_at IS NULL OR reported_amount IS DISTINCT FROM total_commission_amount)
                RETURNING
                    code,
                    total_commission_amount,
                    currency,
                    payment_method,
                    status,
                    reported_at
                """,
                (
                    payment_method,
                    payload.payment_reference,
                    payload.proof_url,
                    settlement_id,
                ),
            )

            updated = cur.fetchone()

            if not updated:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "No fue posible registrar "
                        "el reporte de pago."
                    ),
                )

        conn.commit()

    return {
        "code": updated[0],
        "partner_code": partner_code,
        "business_name": business_name,
        "total_commission_amount": float(updated[1]),
        "currency": updated[2].strip(),
        "payment_method": updated[3],
        "status": updated[4],
        "reported_at": updated[5],
        "verified": False,
        "paid": False,
    }
@router.post("/{settlement_code}/verify-payment", status_code=200, dependencies=[authorize("settlement.verify")])
def verify_settlement_payment(settlement_code: str):

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('h4u.partner_actor',%s,true)", (audit_actor()[0],))
            # Mismo orden que creación y vencimientos: partner antes del cierre.
            cur.execute("SELECT partner_id FROM partner_settlements WHERE code = %s", (settlement_code,))
            owner = cur.fetchone()
            if not owner:
                raise HTTPException(404, "Settlement no encontrado.")
            cur.execute("SELECT id FROM partners WHERE id = %s FOR UPDATE", (owner[0],))
            cur.fetchone()

            # 1. Bloquear el settlement.
            cur.execute(
                """
                SELECT
                    ps.id,
                    ps.code,
                    ps.partner_id,
                    ps.total_commission_amount,
                    ps.currency,
                    ps.status,
                    ps.reported_at,
                    ps.verified_at,
                    ps.paid_at,
                    p.code,
                    p.business_name,
                    ps.reported_amount
                FROM partner_settlements ps
                JOIN partners p
                    ON p.id = ps.partner_id
                WHERE ps.code = %s
                FOR UPDATE OF ps
                """,
                (settlement_code,),
            )

            settlement = cur.fetchone()

            if not settlement:
                raise HTTPException(
                    status_code=404,
                    detail="Settlement no encontrado.",
                )

            settlement_id = settlement[0]
            status = settlement[5]
            reported_at = settlement[6]
            verified_at = settlement[7]
            paid_at = settlement[8]
            partner_code = settlement[9]
            business_name = settlement[10]

            # 2. Evitar una segunda verificación.
            if status == "paid":
                return {
                    "code": settlement[1],
                    "partner_code": partner_code,
                    "business_name": business_name,
                    "status": status,
                    "verified_at": verified_at,
                    "paid_at": paid_at,
                    "already_verified": True,
                    "verified": True,
                    "paid": True,
                }

            validate_settlement_balance(cur, settlement_id)
            zero_balance = settlement[3] == 0
            if (status != "waiting_verification"
                    and not (zero_balance and status in {"open", "pending_payment", "overdue"})):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "El settlement no está esperando "
                        "verificación de pago."
                    ),
                )

            if reported_at is None and not zero_balance:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "El partner todavía no ha "
                        "reportado el pago."
                    ),
                )

            if settlement[11] is not None and settlement[11] != settlement[3]:
                raise HTTPException(409, "El refund cambió el saldo; se requiere un nuevo reporte conciliado.")
            snapshot_settlement(cur, settlement_id, "before_verification")

            # 3. Bloquear las comisiones incluidas.
            cur.execute(
                """
                SELECT
                    c.id,
                    c.code,
                    c.status
                FROM settlement_commissions sc
                JOIN commissions c
                    ON c.id = sc.commission_id
                WHERE sc.settlement_id = %s
                ORDER BY c.id
                FOR UPDATE OF c
                """,
                (settlement_id,),
            )

            commissions = cur.fetchall()

            if not commissions:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "El settlement no contiene "
                        "comisiones."
                    ),
                )

            invalid_commissions = [
                row[1]
                for row in commissions
                if row[2] != "earned"
            ]

            if invalid_commissions:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Existen comisiones que no están "
                        "en estado earned."
                    ),
                )

            # 4. Marcar el settlement como pagado.
            cur.execute(
                """
                UPDATE partner_settlements
                SET
                    status = 'paid',
                    verified_at = now(),
                    paid_at = now(),
                    updated_at = now()
                WHERE id = %s
                  AND status IN ('waiting_verification', 'open', 'pending_payment', 'overdue')
                RETURNING
                    code,
                    total_commission_amount,
                    currency,
                    status,
                    verified_at,
                    paid_at
                """,
                (settlement_id,),
            )

            updated_settlement = cur.fetchone()

            if not updated_settlement:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "No fue posible verificar "
                        "el settlement."
                    ),
                )

            # 5. Las comisiones pasan de
            # earned → settled.
            cur.execute(
                """
                UPDATE commissions c
                SET
                    status = 'settled',
                    settled_at = now(),
                    updated_at = now()
                FROM settlement_commissions sc
                WHERE sc.settlement_id = %s
                  AND sc.commission_id = c.id
                  AND c.status = 'earned'
                RETURNING
                    c.id,
                    c.code
                """,
                (settlement_id,),
            )

            settled_commissions = cur.fetchall()

            if len(settled_commissions) != len(commissions):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "No fue posible liquidar todas "
                        "las comisiones del settlement."
                    ),
                )
            # 6. Comprobar si el partner mantiene
            # otras deudas vencidas.
            cur.execute(
                """
                SELECT COUNT(*)
                FROM partner_settlements
                WHERE partner_id = %s
                  AND (
                        status = 'overdue'
                        OR (
                            status = 'pending_payment'
                            AND due_date < CURRENT_DATE
                        )
                  )
                """,
                (settlement[2],),
            )

            remaining_overdue_debts = cur.fetchone()[0]

            partner_reactivated = False

            # 7. Reactivar únicamente si:
            # - fue suspendido previamente
            # - ya no mantiene deuda vencida.
            if remaining_overdue_debts == 0:
                cur.execute(
                    """
                    UPDATE partners
                    SET
                        status = 'active',
                        suspension_source = NULL,
                        updated_at = now()
                    WHERE id = %s
                      AND status = 'suspended'
                      AND suspension_source = 'debt'
                      AND EXISTS (SELECT 1 FROM partner_memberships m JOIN users u ON u.id=m.user_id
                          WHERE m.partner_id=partners.id AND m.membership_role='owner'
                          AND m.status='active' AND u.role='partner' AND u.status='active')
                    RETURNING code
                    """,
                    (settlement[2],),
                )

                reactivated_partner = cur.fetchone()

                if reactivated_partner:
                    partner_reactivated = True
        conn.commit()

    return {
        "code": updated_settlement[0],
        "partner_code": partner_code,
        "business_name": business_name,
        "total_commission_amount": float(
            updated_settlement[1]
        ),
        "currency": updated_settlement[2].strip(),
        "status": updated_settlement[3],
        "verified_at": updated_settlement[4],
        "paid_at": updated_settlement[5],
        "settled_commissions": len(
            settled_commissions
        ),
        "settled_without_transfer": zero_balance,
        "already_verified": False,
        "verified": True,
        "paid": True,
        "remaining_overdue_debts": remaining_overdue_debts,
        "partner_reactivated": partner_reactivated,
    }

@router.post("/process-overdue", status_code=200, dependencies=[authorize("settlement.overdue")])
def process_overdue_settlements():

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('h4u.partner_actor',%s,true)", (audit_actor()[0],))
            # Fijar primero el conjunto de partners y bloquearlo en orden estable.
            cur.execute("""
                SELECT p.id FROM partners p
                WHERE EXISTS (
                    SELECT 1 FROM partner_settlements ps
                    WHERE ps.partner_id = p.id AND ps.status = 'pending_payment'
                      AND ps.due_date < CURRENT_DATE
                ) ORDER BY p.id FOR UPDATE OF p
            """)
            locked_partner_ids = [row[0] for row in cur.fetchall()]

            # 1. Buscar y bloquear settlements realmente vencidos.
            cur.execute(
                """
                SELECT
                    ps.id,
                    ps.code,
                    ps.partner_id,
                    ps.due_date
                FROM partner_settlements ps
                WHERE ps.status = 'pending_payment'
                  AND ps.due_date < CURRENT_DATE
                  AND ps.partner_id = ANY(%s)
                ORDER BY ps.due_date, ps.id
                FOR UPDATE OF ps
                """,
                (locked_partner_ids,),
            )

            overdue_rows = cur.fetchall()

            if not overdue_rows:
                return {
                    "processed_settlements": 0,
                    "suspended_partners": 0,
                    "settlements": [],
                    "partners": [],
                }

            settlement_ids = [
                row[0] for row in overdue_rows
            ]

            partner_ids = list(
                {row[2] for row in overdue_rows}
            )

            # 2. Marcar settlements como overdue.
            cur.execute(
                """
                UPDATE partner_settlements
                SET
                    status = 'overdue',
                    updated_at = now()
                WHERE id = ANY(%s)
                  AND status = 'pending_payment'
                RETURNING code
                """,
                (settlement_ids,),
            )

            updated_settlements = [
                row[0] for row in cur.fetchall()
            ]

            # 3. Bloquear partners afectados.
            cur.execute(
                """
                SELECT id
                FROM partners
                WHERE id = ANY(%s)
                ORDER BY id
                FOR UPDATE
                """,
                (partner_ids,),
            )
            cur.fetchall()

            # 4. Suspender únicamente partners ACTIVE.
            cur.execute(
                """
                UPDATE partners
                SET
                    status = 'suspended',
                    suspension_source = 'debt',
                    updated_at = now()
                WHERE id = ANY(%s)
                  AND status = 'active'
                RETURNING code
                """,
                (partner_ids,),
            )

            suspended_partners = [
                row[0] for row in cur.fetchall()
            ]

        conn.commit()

    return {
        "processed_settlements": len(
            updated_settlements
        ),
        "suspended_partners": len(
            suspended_partners
        ),
        "settlements": updated_settlements,
        "partners": suspended_partners,
    }
