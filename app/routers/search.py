from typing import Optional

from fastapi import APIRouter, Query, HTTPException
from app.db import get_connection


router = APIRouter(
    prefix="/search",
    tags=["Search"]
)


@router.get("")
def global_search(
    q: str = Query(..., min_length=2),
    category: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
):
    term = f"%{q}%"

    allowed_categories = {
        "hotel",
        "restaurant",
        "tour",
        "attraction",
    }

    if category and category not in allowed_categories:
        raise HTTPException(
            status_code=400,
            detail="category debe ser: hotel, restaurant, tour o attraction"
        )

    results = []

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:

                # -------------------------
                # HOTELS
                # -------------------------
                if category in (None, "hotel"):

                    cur.execute(
                        """
                        SELECT
                            code,
                            name,
                            address,
                            rating
                        FROM hotels
                        WHERE status = 'active'
                          AND (
                              COALESCE(name, '') ILIKE %s
                              OR COALESCE(category, '') ILIKE %s
                              OR COALESCE(address, '') ILIKE %s
                          )
                        ORDER BY rating DESC NULLS LAST, name
                        LIMIT %s
                        """,
                        (term, term, term, limit),
                    )

                    for row in cur.fetchall():
                        results.append({
                            "code": row[0],
                            "name": row[1],
                            "category": "hotel",
                            "location": row[2],
                            "rating": (
                                float(row[3])
                                if row[3] is not None
                                else None
                            ),
                        })

                # -------------------------
                # RESTAURANTS
                # -------------------------
                if category in (None, "restaurant"):

                    cur.execute(
                        """
                        SELECT
                            code,
                            name,
                            address,
                            rating
                        FROM restaurants
                        WHERE status = 'active'
                          AND (
                              COALESCE(name, '') ILIKE %s
                              OR COALESCE(category, '') ILIKE %s
                              OR COALESCE(place_type, '') ILIKE %s
                              OR COALESCE(address, '') ILIKE %s
                          )
                        ORDER BY rating DESC NULLS LAST, name
                        LIMIT %s
                        """,
                        (term, term, term, term, limit),
                    )

                    for row in cur.fetchall():
                        results.append({
                            "code": row[0],
                            "name": row[1],
                            "category": "restaurant",
                            "location": row[2],
                            "rating": (
                                float(row[3])
                                if row[3] is not None
                                else None
                            ),
                        })

                # -------------------------
                # TOURS
                # -------------------------
                if category in (None, "tour"):

                    cur.execute(
                        """
                        SELECT
                            code,
                            name,
                            destination_label
                        FROM tours
                        WHERE status = 'active'
                          AND (
                              COALESCE(name, '') ILIKE %s
                              OR COALESCE(description, '') ILIKE %s
                              OR COALESCE(destination_label, '') ILIKE %s
                              OR COALESCE(languages, '') ILIKE %s
                          )
                        ORDER BY name
                        LIMIT %s
                        """,
                        (term, term, term, term, limit),
                    )

                    for row in cur.fetchall():
                        results.append({
                            "code": row[0],
                            "name": row[1],
                            "category": "tour",
                            "location": row[2],
                            "rating": None,
                        })

                # -------------------------
                # ATTRACTIONS
                # -------------------------
                if category in (None, "attraction"):

                    cur.execute(
                        """
                        SELECT
                            code,
                            name,
                            zone
                        FROM attractions
                        WHERE status = 'active'
                          AND (
                              COALESCE(name, '') ILIKE %s
                              OR COALESCE(category, '') ILIKE %s
                              OR COALESCE(zone, '') ILIKE %s
                              OR COALESCE(description, '') ILIKE %s
                          )
                        ORDER BY name
                        LIMIT %s
                        """,
                        (term, term, term, term, limit),
                    )

                    for row in cur.fetchall():
                        results.append({
                            "code": row[0],
                            "name": row[1],
                            "category": "attraction",
                            "location": row[2],
                            "rating": None,
                        })

        return {
            "query": q,
            "category": category,
            "total": len(results),
            "results": results[:limit],
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )
