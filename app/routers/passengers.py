from datetime import date
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import get_connection


router = APIRouter(
    prefix="/reservations",
    tags=["Passengers"],
)


class PassengerCreate(BaseModel):
    passenger_number: int = Field(ge=1)
    first_name: str
    last_name: str
    birth_date: date
    nationality_code: str
    document_type: str
    document_number: str
    is_primary_passenger: bool = False


class PassengersCreate(BaseModel):
    passengers: List[PassengerCreate]


@router.post("/{reservation_code}/passengers", status_code=201)
def create_passengers(
    reservation_code: str,
    payload: PassengersCreate,
):

    if not payload.passengers:
        raise HTTPException(
            status_code=400,
            detail="Debe enviarse al menos un pasajero.",
        )

    # No permitir números de pasajero repetidos
    # dentro del mismo request HTTP.
    passenger_numbers = [
        passenger.passenger_number
        for passenger in payload.passengers
    ]

    if len(passenger_numbers) != len(set(passenger_numbers)):
        raise HTTPException(
            status_code=400,
            detail="Hay passenger_number repetidos.",
        )

    primary_count = sum(
        1
        for passenger in payload.passengers
        if passenger.is_primary_passenger
    )

    if primary_count > 1:
        raise HTTPException(
            status_code=400,
            detail="Solo puede existir un pasajero principal.",
        )

    with get_connection() as conn:
        with conn.cursor() as cur:

            # 1. Bloquear la reserva mientras registramos
            # los pasajeros.
            #
            # También consultamos requires_payment para decidir
            # el siguiente estado cuando estén completos.
            cur.execute(
                """
                SELECT
                    r.id,
                    r.service_request_id,
                    r.passenger_count,
                    r.status,
                    p.requires_payment
                FROM reservations r
                JOIN products p
                    ON p.id = r.product_id
                WHERE r.code = %s
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
            service_request_id = reservation[1]
            expected_passengers = reservation[2]
            reservation_status = reservation[3]
            requires_payment = reservation[4]

            if reservation_status != "awaiting_passenger_data":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "La reserva no está esperando "
                        "datos de pasajeros."
                    ),
                )

            # 2. Validar cada pasajero.
            for passenger in payload.passengers:

                if passenger.passenger_number > expected_passengers:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"passenger_number "
                            f"{passenger.passenger_number} "
                            f"supera el total de pasajeros."
                        ),
                    )

                nationality_code = (
                    passenger.nationality_code
                    .strip()
                    .upper()
                )

                if len(nationality_code) != 2:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "nationality_code debe tener "
                            "2 caracteres."
                        ),
                    )

                document_type = (
                    passenger.document_type
                    .strip()
                    .lower()
                )

                if document_type not in {
                    "dni",
                    "passport",
                    "foreign_id",
                    "other",
                }:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "document_type debe ser dni, "
                            "passport, foreign_id u other."
                        ),
                    )

                # UPSERT permite corregir los datos de un
                # pasajero mientras la reserva siga esperando
                # información.
                cur.execute(
                    """
                    INSERT INTO request_passengers (
                        service_request_id,
                        passenger_number,
                        first_name,
                        last_name,
                        birth_date,
                        nationality_code,
                        document_type,
                        document_number,
                        is_primary_passenger
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
                        %s
                    )
                    ON CONFLICT (
                        service_request_id,
                        passenger_number
                    )
                    DO UPDATE SET
                        first_name = EXCLUDED.first_name,
                        last_name = EXCLUDED.last_name,
                        birth_date = EXCLUDED.birth_date,
                        nationality_code =
                            EXCLUDED.nationality_code,
                        document_type =
                            EXCLUDED.document_type,
                        document_number =
                            EXCLUDED.document_number,
                        is_primary_passenger =
                            EXCLUDED.is_primary_passenger,
                        updated_at = now()
                    RETURNING id
                    """,
                    (
                        service_request_id,
                        passenger.passenger_number,
                        passenger.first_name.strip(),
                        passenger.last_name.strip(),
                        passenger.birth_date,
                        nationality_code,
                        document_type,
                        passenger.document_number.strip(),
                        passenger.is_primary_passenger,
                    ),
                )

            # 3. Contar pasajeros registrados.
            cur.execute(
                """
                SELECT COUNT(*)
                FROM request_passengers
                WHERE service_request_id = %s
                """,
                (service_request_id,),
            )

            registered_passengers = cur.fetchone()[0]

            # 4. Si ya tenemos todos los pasajeros,
            # avanzar según la política de pago del producto.
            new_status = reservation_status

            if registered_passengers == expected_passengers:

                if requires_payment:
                    next_status = "payment_pending"
                else:
                    next_status = "confirmed"

                cur.execute(
                    """
                    UPDATE reservations
                    SET
                        status = %s,
                        confirmed_at = CASE
                            WHEN %s = 'confirmed'
                            THEN COALESCE(confirmed_at, now())
                            ELSE confirmed_at
                        END,
                        updated_at = now()
                    WHERE id = %s
                      AND status = 'awaiting_passenger_data'
                    RETURNING status
                    """,
                    (
                        next_status,
                        next_status,
                        reservation_id,
                    ),
                )

                updated = cur.fetchone()

                if updated:
                    new_status = updated[0]

        conn.commit()

    return {
        "reservation_code": reservation_code,
        "expected_passengers": expected_passengers,
        "registered_passengers": registered_passengers,
        "status": new_status,
        "ready_for_payment": (
            new_status == "payment_pending"
        ),
        "payment_required": requires_payment,
    }