from datetime import date, time
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Header, Depends
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb
from app.services.request_idempotency import fingerprint, replay, validate_destination_time
from pydantic import BaseModel, Field, ConfigDict

from app.services.commercial_eligibility import product_accepts_requests, ELIGIBLE_REQUEST_PARTNER_SQL
from app.db import get_connection
from app.auth import authorize, recheck_owner, recheck_principal, Principal, get_current_actor


router = APIRouter(
    prefix="/service-requests",
    tags=["Service Requests"],
)


class ServiceRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: UUID
    product_id: UUID

    request_type: str = "request"

    service_date: date
    preferred_time: Optional[time] = None
    flexible_time: bool = True

    passenger_count: int = Field(ge=1)
    adults_count: int = Field(ge=0)
    minors_count: int = Field(ge=0)

    additional_notes: Optional[str] = None

    # Solo para pruebas controladas.
    # Nunca genera notificaciones externas.
    simulation: bool = False


@router.post("", status_code=201, dependencies=[authorize("request.create")])
def submit_service_request(payload: ServiceRequestCreate,
                           idempotency_key: str = Header(min_length=1, max_length=200),
                           actor: Principal = Depends(get_current_actor)):
    if not idempotency_key.strip() or idempotency_key != idempotency_key.strip():
        raise HTTPException(422, 'Clave de idempotencia no válida.')
    return create_service_request(payload, idempotency_key, actor)


def create_service_request(payload: ServiceRequestCreate, idempotency_key=None, actor=None):
    """Trusted in-process fixtures may omit a key; the HTTP entry always requires it."""

    if payload.request_type not in {"request", "reservation"}:
        raise HTTPException(
            status_code=400,
            detail="request_type debe ser request o reservation.",
        )

    if (
        payload.passenger_count
        != payload.adults_count + payload.minors_count
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "passenger_count debe ser igual a "
                "adults_count + minors_count."
            ),
        )

    with get_connection() as conn:
        with conn.cursor() as cur:
            if actor is not None:
                recheck_principal(cur, actor)
            if payload.simulation:
                cur.execute("SET LOCAL h4u.messaging_simulation = 'true'")

            # 1. Validar sesión activa.
            cur.execute(
                """
                SELECT
                    s.id,
                    s.traveler_id,
                    s.destination_id, s.status, s.ended_at
                FROM sessions s
                WHERE s.id = %s
                FOR SHARE OF s
                """,
                (payload.session_id,),
            )

            session = cur.fetchone()

            if not session:
                raise HTTPException(
                    status_code=404,
                    detail="Sesión activa no encontrada.",
                )

            recheck_owner(cur, "session", payload.session_id)
            traveler_id = session[1]
            destination_id = session[2]

            if traveler_id is None:
                raise HTTPException(403, 'Se requiere una sesión con propietario.')
            digest = fingerprint(payload)
            if idempotency_key is not None:
                previous = replay(cur, traveler_id, idempotency_key, digest)
                if previous is not None:
                    return previous
            if session[3] != 'active' or session[4] is not None:
                raise HTTPException(409, 'La sesión comercial está cerrada.')
            cur.execute('SELECT timezone,status FROM destinations WHERE id=%s FOR SHARE', (destination_id,))
            destination = cur.fetchone()
            if not destination or destination[1] != 'active':
                raise HTTPException(409, 'Destino no disponible.')
            validate_destination_time(payload, destination[0])

            # 2. Validar producto.
            cur.execute(
                """
                SELECT
                    p.id,
                    p.destination_id,
                    p.name,
                    p.status,
                    p.reservations_enabled
                FROM products p
                WHERE p.id = %s
                FOR SHARE OF p
                """,
                (payload.product_id,),
            )

            product = cur.fetchone()

            if not product:
                raise HTTPException(
                    status_code=404,
                    detail="Producto no encontrado.",
                )

            if product[1] != destination_id:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "El producto no pertenece al destino "
                        "de la sesión."
                    ),
                )

            # 3. Reglas comerciales del producto.
            # En producción solo se permiten productos activos.
            if not payload.simulation:
                if not product_accepts_requests(product[3], product[4]):
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "El producto no está habilitado "
                            "para recibir solicitudes."
                        ),
                    )

            # 4. Buscar partners candidatos.
            if payload.simulation:
                # Modo controlado:
                # permite nuestro candidato pending,
                # pero no envía notificaciones externas.
                cur.execute(
                    """
                    SELECT
                        pp.id,
                        pp.partner_id,
                        p.code,
                        p.business_name,
                        p.status,
                        pp.priority
                    FROM product_partners pp
                    JOIN partners p
                        ON p.id = pp.partner_id
                    WHERE pp.product_id = %s
                      AND pp.status = 'active'
                      AND p.status IN ('pending', 'active')
                    ORDER BY
                        pp.priority DESC,
                        p.business_name, p.id
                    FOR SHARE OF pp,p
                    """,
                    (payload.product_id,),
                )

            else:
                # Producción:
                # solamente partners comercialmente habilitados.
                cur.execute(
                    f"""
                    SELECT
                        pp.id,
                        pp.partner_id,
                        p.code,
                        p.business_name,
                        p.status,
                        pp.priority
                    FROM product_partners pp
                    JOIN partners p
                        ON p.id = pp.partner_id
                    WHERE pp.product_id = %s
                      AND {ELIGIBLE_REQUEST_PARTNER_SQL}
                    ORDER BY
                        pp.priority DESC,
                        p.business_name, p.id
                    FOR SHARE OF pp,p
                    """,
                    (payload.product_id,),
                )

            candidates = cur.fetchall()
            if not candidates:
                raise HTTPException(409, 'No hay partners elegibles para este producto.')

            # 5. Generar código único.
            cur.execute(
                """
                SELECT
                    'SR-' ||
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

            request_code = cur.fetchone()[0]

            # Si existen candidatos, comienza búsqueda.
            # Si no existen, conservamos created.
            initial_status = (
                "searching"
                if candidates
                else "created"
            )

            # 6. Crear solicitud.
            cur.execute(
                """
                INSERT INTO service_requests (
                    code,
                    session_id,
                    traveler_id,
                    destination_id,
                    product_id,
                    request_type,
                    service_date,
                    preferred_time,
                    flexible_time,
                    passenger_count,
                    adults_count,
                    minors_count,
                    additional_notes,
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
                    %s,
                    %s,
                    %s
                )
                RETURNING
                    id,
                    code,
                    status,
                    created_at
                """,
                (
                    request_code,
                    payload.session_id,
                    traveler_id,
                    destination_id,
                    payload.product_id,
                    payload.request_type,
                    payload.service_date,
                    payload.preferred_time,
                    payload.flexible_time,
                    payload.passenger_count,
                    payload.adults_count,
                    payload.minors_count,
                    payload.additional_notes,
                    initial_status,
                ),
            )

            created = cur.fetchone()

            # 7. Crear request_partners.
            #
            # Importante:
            # usamos estado 'sent' para representar que
            # el candidato fue seleccionado internamente.
            # Todavía NO existe envío real por WhatsApp.
            selected_partners = []

            for candidate in candidates:
                product_partner_id = candidate[0]
                partner_id = candidate[1]
                partner_code = candidate[2]
                business_name = candidate[3]

                cur.execute(
                    """
                    INSERT INTO request_partners (
                        service_request_id,
                        partner_id,
                        product_partner_id,
                        status,
                        sent_at
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        'sent',
                        now()
                    )
                    RETURNING id
                    """,
                    (
                        created[0],
                        partner_id,
                        product_partner_id,
                    ),
                )

                request_partner_id = cur.fetchone()[0]

                selected_partners.append(
                    {
                        "request_partner_id": str(
                            request_partner_id
                        ),
                        "partner_code": partner_code,
                        "business_name": business_name,
                        "notification_sent": False,
                    }
                )

        result = jsonable_encoder({
            "id": str(created[0]),
            "code": created[1],
            "status": created[2],
            "simulation": payload.simulation,
            "created_at": created[3],
            "product": {
                "id": str(product[0]),
                "name": product[2],
            },
            "passengers": {
                "total": payload.passenger_count,
                "adults": payload.adults_count,
                "minors": payload.minors_count,
            },
            "candidate_count": len(selected_partners),
            "candidates": selected_partners,
        })
        if idempotency_key is not None:
            conn.execute('''INSERT INTO service_request_idempotency
                (traveler_id,idempotency_key,request_fingerprint,service_request_id,response_status,response_body)
                VALUES (%s,%s,%s,%s,201,%s)''',
                (traveler_id,idempotency_key,digest,created[0],Jsonb(result)))
        conn.commit()
        return result
