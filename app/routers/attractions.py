from typing import Optional

from fastapi import APIRouter, Query
from app.db import get_connection


router = APIRouter(
    prefix="/attractions",
    tags=["Attractions"]
)


@router.get("")
def get_attractions(
    destination: str = Query(default="PARACAS"),
    search: Optional[str] = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100)
):
    with get_connection() as conn:
        with conn.cursor() as cur:

            sql = """
                SELECT
                    a.code,
                    a.name,
                    a.category,
                    a.zone,
                    a.description,
                    a.opening_hours,
                    a.adult_price,
                    a.child_price,
                    a.currency,
                    a.latitude,
                    a.longitude,
                    a.verification_status,
                    a.status
                FROM attractions a
                JOIN destinations d
                    ON d.id = a.destination_id
                WHERE d.code = %s
                  AND a.status = 'active'
            """

            params = [destination]

            if search:
                sql += """
                    AND (
                        lower(COALESCE(a.name, '')) LIKE lower(%s)
                        OR lower(COALESCE(a.category, '')) LIKE lower(%s)
                        OR lower(COALESCE(a.zone, '')) LIKE lower(%s)
                        OR lower(COALESCE(a.description, '')) LIKE lower(%s)
                    )
                """

                term = f"%{search}%"
                params.extend([term, term, term, term])

            sql += """
                ORDER BY a.name
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
            "category": r[2],
            "zone": r[3],
            "description": r[4],
            "opening_hours": r[5],
            "adult_price": float(r[6]) if r[6] is not None else None,
            "child_price": float(r[7]) if r[7] is not None else None,
            "currency": r[8],
            "latitude": float(r[9]) if r[9] is not None else None,
            "longitude": float(r[10]) if r[10] is not None else None,
            "verification_status": r[11],
            "status": r[12],
        }
        for r in rows
    ]
