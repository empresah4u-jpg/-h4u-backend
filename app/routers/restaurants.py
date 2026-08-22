from typing import Optional

from fastapi import APIRouter, Query
from app.db import get_connection


router = APIRouter(
    prefix="/restaurants",
    tags=["Restaurants"]
)


@router.get("")
def get_restaurants(
    destination: str = Query(default="PARACAS"),
    search: Optional[str] = Query(default=None),
    place_type: Optional[str] = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100)
):
    with get_connection() as conn:
        with conn.cursor() as cur:

            sql = """
                SELECT
                    r.code,
                    r.name,
                    r.place_type,
                    r.category,
                    r.address,
                    r.phone,
                    r.opening_hours,
                    r.price_range,
                    r.currency,
                    r.rating,
                    r.reviews_count,
                    r.latitude,
                    r.longitude,
                    r.verification_status,
                    r.status
                FROM restaurants r
                JOIN destinations d
                    ON d.id = r.destination_id
                WHERE d.code = %s
                  AND r.status = 'active'
            """

            params = [destination]

            if search:
                sql += """
                    AND (
                        lower(COALESCE(r.name, '')) LIKE lower(%s)
                        OR lower(COALESCE(r.category, '')) LIKE lower(%s)
                        OR lower(COALESCE(r.address, '')) LIKE lower(%s)
                        OR lower(COALESCE(r.place_type, '')) LIKE lower(%s)
                    )
                """

                term = f"%{search}%"
                params.extend([term, term, term, term])

            if place_type:
                sql += """
                    AND lower(r.place_type) = lower(%s)
                """
                params.append(place_type)

            sql += """
                ORDER BY r.rating DESC NULLS LAST, r.name
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
            "place_type": r[2],
            "category": r[3],
            "address": r[4],
            "phone": r[5],
            "opening_hours": r[6],
            "price_range": r[7],
            "currency": r[8],
            "rating": float(r[9]) if r[9] is not None else None,
            "reviews_count": r[10],
            "latitude": float(r[11]) if r[11] is not None else None,
            "longitude": float(r[12]) if r[12] is not None else None,
            "verification_status": r[13],
            "status": r[14],
        }
        for r in rows
    ]
