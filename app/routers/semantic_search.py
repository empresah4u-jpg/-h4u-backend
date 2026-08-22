from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sentence_transformers import SentenceTransformer

from app.db import get_connection
from app.logger import get_logger


router = APIRouter(
    prefix="/semantic-search",
    tags=["Semantic Search"],
)

logger = get_logger(__name__)

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
model = SentenceTransformer(MODEL_NAME)


def vector_to_pg(vector):
    return "[" + ",".join(str(float(x)) for x in vector) + "]"


@router.get("")
def semantic_search(
    q: str = Query(..., min_length=2),
    category: Optional[str] = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
):
    allowed_categories = {
        "hotel",
        "restaurant",
        "tour",
        "attraction",
    }

    if category and category not in allowed_categories:
        logger.warning(
            "semantic_search_invalid_category category=%s",
            category,
        )

        raise HTTPException(
            status_code=400,
            detail=(
                "category debe ser: "
                "hotel, restaurant, tour o attraction"
            ),
        )

    try:
        query_embedding = model.encode(
            q,
            normalize_embeddings=True,
        )

        pg_vector = vector_to_pg(query_embedding)

        with get_connection() as conn:
            with conn.cursor() as cur:

                sql = """
                    SELECT
                        entity_type,
                        entity_id,
                        content,
                        1 - (embedding <=> %s::vector) AS similarity
                    FROM entity_embeddings
                    WHERE embedding IS NOT NULL
                """

                params = [pg_vector]

                if category:
                    sql += " AND entity_type = %s"
                    params.append(category)

                sql += """
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                """

                params.extend([pg_vector, limit])

                cur.execute(sql, params)
                semantic_rows = cur.fetchall()

                results = []

                for (
                    entity_type,
                    entity_id,
                    content,
                    similarity,
                ) in semantic_rows:

                    if entity_type == "hotel":
                        cur.execute(
                            """
                            SELECT
                                code,
                                name,
                                category,
                                address,
                                rating,
                                reviews_count,
                                price_observed,
                                currency,
                                latitude,
                                longitude,
                                website,
                                verification_status
                            FROM hotels
                            WHERE id = %s
                            """,
                            (entity_id,),
                        )

                        row = cur.fetchone()

                        if row:
                            results.append(
                                {
                                    "entity_type": "hotel",
                                    "code": row[0],
                                    "name": row[1],
                                    "category": row[2],
                                    "address": row[3],
                                    "rating": (
                                        float(row[4])
                                        if row[4] is not None
                                        else None
                                    ),
                                    "reviews_count": row[5],
                                    "price": (
                                        float(row[6])
                                        if row[6] is not None
                                        else None
                                    ),
                                    "currency": row[7],
                                    "latitude": (
                                        float(row[8])
                                        if row[8] is not None
                                        else None
                                    ),
                                    "longitude": (
                                        float(row[9])
                                        if row[9] is not None
                                        else None
                                    ),
                                    "website": row[10],
                                    "verification_status": row[11],
                                    "similarity": round(
                                        float(similarity),
                                        4,
                                    ),
                                }
                            )

                    elif entity_type == "restaurant":
                        cur.execute(
                            """
                            SELECT
                                code,
                                name,
                                place_type,
                                category,
                                address,
                                rating,
                                reviews_count,
                                price_range,
                                currency,
                                latitude,
                                longitude,
                                verification_status
                            FROM restaurants
                            WHERE id = %s
                            """,
                            (entity_id,),
                        )

                        row = cur.fetchone()

                        if row:
                            results.append(
                                {
                                    "entity_type": "restaurant",
                                    "code": row[0],
                                    "name": row[1],
                                    "place_type": row[2],
                                    "category": row[3],
                                    "address": row[4],
                                    "rating": (
                                        float(row[5])
                                        if row[5] is not None
                                        else None
                                    ),
                                    "reviews_count": row[6],
                                    "price_range": row[7],
                                    "currency": row[8],
                                    "latitude": (
                                        float(row[9])
                                        if row[9] is not None
                                        else None
                                    ),
                                    "longitude": (
                                        float(row[10])
                                        if row[10] is not None
                                        else None
                                    ),
                                    "verification_status": row[11],
                                    "similarity": round(
                                        float(similarity),
                                        4,
                                    ),
                                }
                            )

                    elif entity_type == "tour":
                        cur.execute(
                            """
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
                                op.code,
                                op.name
                            FROM tours t
                            LEFT JOIN tour_operators op
                                ON op.id = t.tour_operator_id
                            WHERE t.id = %s
                            """,
                            (entity_id,),
                        )

                        row = cur.fetchone()

                        if row:
                            results.append(
                                {
                                    "entity_type": "tour",
                                    "code": row[0],
                                    "name": row[1],
                                    "description": row[2],
                                    "duration_minutes": row[3],
                                    "price": (
                                        float(row[4])
                                        if row[4] is not None
                                        else None
                                    ),
                                    "currency": row[5],
                                    "languages": row[6],
                                    "meeting_point": row[7],
                                    "booking_url": row[8],
                                    "verification_status": row[9],
                                    "operator": (
                                        {
                                            "code": row[10],
                                            "name": row[11],
                                        }
                                        if row[10]
                                        else None
                                    ),
                                    "similarity": round(
                                        float(similarity),
                                        4,
                                    ),
                                }
                            )

                    elif entity_type == "attraction":
                        cur.execute(
                            """
                            SELECT
                                code,
                                name,
                                category,
                                zone,
                                description,
                                opening_hours,
                                adult_price,
                                child_price,
                                currency,
                                latitude,
                                longitude,
                                verification_status
                            FROM attractions
                            WHERE id = %s
                            """,
                            (entity_id,),
                        )

                        row = cur.fetchone()

                        if row:
                            results.append(
                                {
                                    "entity_type": "attraction",
                                    "code": row[0],
                                    "name": row[1],
                                    "category": row[2],
                                    "zone": row[3],
                                    "description": row[4],
                                    "opening_hours": row[5],
                                    "adult_price": (
                                        float(row[6])
                                        if row[6] is not None
                                        else None
                                    ),
                                    "child_price": (
                                        float(row[7])
                                        if row[7] is not None
                                        else None
                                    ),
                                    "currency": row[8],
                                    "latitude": (
                                        float(row[9])
                                        if row[9] is not None
                                        else None
                                    ),
                                    "longitude": (
                                        float(row[10])
                                        if row[10] is not None
                                        else None
                                    ),
                                    "verification_status": row[11],
                                    "similarity": round(
                                        float(similarity),
                                        4,
                                    ),
                                }
                            )

        logger.info(
            "semantic_search query=%s category=%s results=%s",
            q,
            category,
            len(results),
        )

        return {
            "query": q,
            "category": category,
            "limit": limit,
            "total": len(results),
            "results": results,
        }

    except HTTPException:
        raise

    except Exception:
        logger.exception(
            "semantic_search_failed query=%s category=%s",
            q,
            category,
        )

        raise HTTPException(
            status_code=500,
            detail="Error interno al procesar la búsqueda semántica",
        )
