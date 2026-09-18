from fastapi import APIRouter, Query
from app.db import get_connection

router = APIRouter(
    prefix="/services",
    tags=["services"],
)


@router.get("")
def get_services(
    destination: str = Query(default="PARACAS"),
    limit: int = Query(default=100, ge=1, le=500),
):
    query = """
        SELECT
            'emergency' AS source_type,
            e.code,
            e.name,
            e.service_type AS category,
            e.address,
            e.phone,
            NULL::text AS opening_hours,
            e.open_24h,
            e.google_place_id,
            e.google_maps_url,
            e.zone,
            e.latitude,
            e.longitude,
            e.verification_status,
            e.confidence_score,
            e.status
        FROM emergency_services e
        JOIN destinations d
          ON d.id = e.destination_id
        WHERE d.code = %s
          AND e.status = 'active'

        UNION ALL

        SELECT
            'general' AS source_type,
            g.code,
            g.name,
            g.category,
            g.address,
            g.phone,
            g.opening_hours,
            NULL::boolean AS open_24h,
            g.google_place_id,
            g.google_maps_url,
            g.zone,
            g.latitude,
            g.longitude,
            g.verification_status,
            g.confidence_score,
            g.status
        FROM general_services g
        JOIN destinations d
          ON d.id = g.destination_id
        WHERE d.code = %s
          AND g.status = 'active'

        ORDER BY name
        LIMIT %s;
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                query,
                (
                    destination,
                    destination,
                    limit,
                ),
            )

            rows = cur.fetchall()
            columns = [desc.name for desc in cur.description]

    return [
        dict(zip(columns, row))
        for row in rows
    ]