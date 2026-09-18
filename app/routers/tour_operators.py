from typing import Optional

from fastapi import APIRouter, Query
from app.db import get_connection


router = APIRouter(
    prefix="/tour-operators",
    tags=["Tour Operators"]
)


@router.get("")
def get_tour_operators(
    destination: str = Query(default="PARACAS"),
    search: Optional[str] = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100)
):
    with get_connection() as conn:
        with conn.cursor() as cur:

            sql = """
                SELECT
                    o.code,
                    o.name,
                    o.address,
                    o.phone,
                    o.email,
                    o.website,
                    o.opening_hours,
                    o.rating,
                    o.reviews_count,
                    o.languages,
                    o.google_place_id,
                    o.google_maps_url,
                    o.zone,
                    o.latitude,
                    o.longitude,
                    o.verification_status,
                    o.confidence_score,
                    o.status
                FROM tour_operators o
                JOIN destinations d
                    ON d.id = o.destination_id
                WHERE d.code = %s
                  AND o.status = 'active'
            """

            params = [destination]

            if search:
                sql += """
                    AND (
                        lower(COALESCE(o.name, '')) LIKE lower(%s)
                        OR lower(COALESCE(o.address, '')) LIKE lower(%s)
                        OR lower(COALESCE(o.zone, '')) LIKE lower(%s)
                    )
                """

                term = f"%{search}%"
                params.extend([term, term, term])

            sql += """
                ORDER BY o.name
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
            "address": r[2],
            "phone": r[3],
            "email": r[4],
            "website": r[5],
            "opening_hours": r[6],
            "rating": float(r[7]) if r[7] is not None else None,
            "reviews_count": r[8],
            "languages": r[9],
            "google_place_id": r[10],
            "google_maps_url": r[11],
            "zone": r[12],
            "latitude": float(r[13]) if r[13] is not None else None,
            "longitude": float(r[14]) if r[14] is not None else None,
            "verification_status": r[15],
            "confidence_score": (
                float(r[16]) if r[16] is not None else None
            ),
            "status": r[17],
        }
        for r in rows
    ]