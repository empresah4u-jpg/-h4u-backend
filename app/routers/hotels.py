from typing import Optional

from fastapi import APIRouter, Query
from app.db import get_connection


router = APIRouter(
    prefix="/hotels",
    tags=["Hotels"]
)


@router.get("")
def get_hotels(
    destination: str = Query(default="PARACAS"),
    search: Optional[str] = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100)
):
    with get_connection() as conn:
        with conn.cursor() as cur:

            base_where = """
                FROM hotels h
                JOIN destinations d
                    ON d.id = h.destination_id
                WHERE d.code = %s
                  AND h.status = 'active'
            """

            params = [destination]

            search_sql = ""

            if search:
                search_sql = """
                    AND (
                        lower(COALESCE(h.name, '')) LIKE lower(%s)
                        OR lower(COALESCE(h.category, '')) LIKE lower(%s)
                        OR lower(COALESCE(h.address, '')) LIKE lower(%s)
                    )
                """

                term = f"%{search}%"
                params.extend([term, term, term])

            count_sql = """
                SELECT COUNT(*)
            """ + base_where + search_sql

            cur.execute(count_sql, params)
            total = cur.fetchone()[0]

            data_sql = """
                SELECT
                    h.code,
                    h.name,
                    h.category,
                    h.address,
                    h.phone,
                    h.website,
                    h.rating,
                    h.reviews_count,
                    h.price_observed,
                    h.currency,
                    h.latitude,
                    h.longitude,
                    h.verification_status,
                    h.status
            """ + base_where + search_sql + """
                ORDER BY h.rating DESC NULLS LAST, h.name
                OFFSET %s
                LIMIT %s
            """

            data_params = params + [offset, limit]

            cur.execute(data_sql, data_params)
            rows = cur.fetchall()

    items = [
        {
            "code": r[0],
            "name": r[1],
            "category": r[2],
            "address": r[3],
            "phone": r[4],
            "website": r[5],
            "rating": float(r[6]) if r[6] is not None else None,
            "reviews_count": r[7],
            "price_observed": float(r[8]) if r[8] is not None else None,
            "currency": r[9],
            "latitude": float(r[10]) if r[10] is not None else None,
            "longitude": float(r[11]) if r[11] is not None else None,
            "verification_status": r[12],
            "status": r[13],
        }
        for r in rows
    ]

    return {
        "items": items,
        "total": total,
        "offset": offset,
        "limit": limit,
    }
