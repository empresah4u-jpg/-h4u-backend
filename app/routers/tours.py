from typing import Optional

from fastapi import APIRouter, Query
from app.db import get_connection


router = APIRouter(
    prefix="/tours",
    tags=["Tours"]
)


@router.get("")
def get_tours(
    destination: str = Query(default="PARACAS"),
    search: Optional[str] = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100)
):
    with get_connection() as conn:
        with conn.cursor() as cur:

            sql = """
                SELECT
                    t.code,
                    t.name,
                    t.description,
                    t.duration_minutes,
                    t.price_from,
                    t.currency,
                    t.languages,
                    t.meeting_point,
                    t.booking_url,
                    t.verification_status,
                    t.status,
                    op.code,
                    op.name
                FROM tours t
                JOIN destinations d
                    ON d.id = t.destination_id
                LEFT JOIN tour_operators op
                    ON op.id = t.tour_operator_id
                WHERE d.code = %s
                  AND t.status = 'active'
            """

            params = [destination]

            if search:
                sql += """
                    AND (
                        lower(COALESCE(t.name, '')) LIKE lower(%s)
                        OR lower(COALESCE(t.description, '')) LIKE lower(%s)
                        OR lower(COALESCE(t.languages, '')) LIKE lower(%s)
                        OR lower(COALESCE(t.meeting_point, '')) LIKE lower(%s)
                        OR lower(COALESCE(op.name, '')) LIKE lower(%s)
                    )
                """

                term = f"%{search}%"
                params.extend([term, term, term, term, term])

            sql += """
                ORDER BY t.name
                OFFSET %s
                LIMIT %s
            """

            params.extend([offset, limit])

            cur.execute(sql, params)
            rows = cur.fetchall()

    return [
        {
            "code": r[0],
            "name": r[1],
            "description": r[2],
            "duration_minutes": r[3],
            "price_from": float(r[4]) if r[4] is not None else None,
            "currency": r[5],
            "languages": r[6],
            "meeting_point": r[7],
            "booking_url": r[8],
            "verification_status": r[9],
            "status": r[10],
            "operator": {
                "code": r[11],
                "name": r[12],
            } if r[11] else None,
        }
        for r in rows
    ]

