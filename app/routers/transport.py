from typing import Optional

from fastapi import APIRouter, Query
from app.db import get_connection


router = APIRouter(
    prefix="/transport",
    tags=["Transport"]
)


@router.get("/routes")
def get_transport_routes(
    search: Optional[str] = Query(default=None),
    origin: Optional[str] = Query(default=None),
    destination: Optional[str] = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100)
):
    with get_connection() as conn:
        with conn.cursor() as cur:

            sql = """
                SELECT
                    tr.code,
                    tr.origin_name,
                    tr.destination_name,
                    tr.service_type,
                    tr.duration_minutes,
                    tr.departure_times,
                    tr.private_service,
                    tr.shared_service,
                    tr.booking_url,
                    tr.status,
                    tp.name
                FROM transport_routes tr
                JOIN transport_providers tp
                    ON tp.id = tr.provider_id
                WHERE tr.status = 'active'
            """

            params = []

            if search:
                sql += """
                    AND (
                        lower(COALESCE(tr.origin_name, '')) LIKE lower(%s)
                        OR lower(COALESCE(tr.destination_name, '')) LIKE lower(%s)
                        OR lower(COALESCE(tr.service_type, '')) LIKE lower(%s)
                        OR lower(COALESCE(tp.name, '')) LIKE lower(%s)
                    )
                """

                term = f"%{search}%"
                params.extend([term, term, term, term])

            if origin:
                sql += """
                    AND lower(COALESCE(tr.origin_name, ''))
                        LIKE lower(%s)
                """
                params.append(f"%{origin}%")

            if destination:
                sql += """
                    AND lower(COALESCE(tr.destination_name, ''))
                        LIKE lower(%s)
                """
                params.append(f"%{destination}%")

            sql += """
                ORDER BY
                    tr.origin_name,
                    tr.destination_name,
                    tp.name
                OFFSET %s
                LIMIT %s
            """

            params.extend([offset, limit])

            cur.execute(sql, params)
            rows = cur.fetchall()

    return [
        {
            "code": r[0],
            "origin": r[1],
            "destination": r[2],
            "service_type": r[3],
            "duration_minutes": r[4],
            "departure_times": r[5],
            "private_service": r[6],
            "shared_service": r[7],
            "booking_url": r[8],
            "status": r[9],
            "provider": r[10],
        }
        for r in rows
    ]
