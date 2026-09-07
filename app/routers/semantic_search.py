import re
import unicodedata
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


MODEL_NAME = (
    "sentence-transformers/"
    "paraphrase-multilingual-MiniLM-L12-v2"
)

model = SentenceTransformer(MODEL_NAME)


STOP_WORDS = {
    # Español
    "quiero",
    "comer",
    "hacer",
    "para",
    "con",
    "una",
    "uno",
    "unos",
    "unas",
    "del",
    "las",
    "los",
    "que",

    # Inglés
    "the",
    "want",
    "near",
    "with",
    "from",
    "this",
    "that",

    # Portugués
    "quero",
    "fazer",
    "uma",
    "um",
    "de",
    "do",
    "da",
    "dos",
    "das",
    "com",
}


def vector_to_pg(vector):
    return "[" + ",".join(
        str(float(x))
        for x in vector
    ) + "]"


def normalize_text(text):
    text = text.lower()

    text = "".join(
        char
        for char in unicodedata.normalize("NFD", text)
        if unicodedata.category(char) != "Mn"
    )

    return re.findall(r"\w+", text)


def get_keywords(query):
    return [
        word
        for word in normalize_text(query)
        if word not in STOP_WORDS
        and len(word) >= 4
    ]


def keyword_score(query, content):
    query_words = get_keywords(query)
    content_words = normalize_text(content)

    if not query_words:
        return 0.0

    matches = 0

    for query_word in query_words:
        for content_word in content_words:

            is_related = (
                content_word.startswith(query_word)
                or query_word.startswith(content_word)
            )

            length_difference = abs(
                len(content_word)
                - len(query_word)
            )

            if is_related and length_difference <= 4:
                matches += 1
                break

    return matches / len(query_words)


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

        # -------------------------------------------------
        # 1. Generar embedding de la consulta
        # -------------------------------------------------

        query_embedding = model.encode(
            q,
            normalize_embeddings=True,
        )

        pg_vector = vector_to_pg(
            query_embedding
        )

        with get_connection() as conn:

            with conn.cursor() as cur:

                # -----------------------------------------
                # 2. Búsqueda semántica
                # -----------------------------------------

                sql = """
                    SELECT
                        entity_type,
                        entity_id,
                        content,
                        1 - (
                            embedding <=> %s::vector
                        ) AS similarity
                    FROM entity_embeddings
                    WHERE embedding IS NOT NULL
                """

                params = [
                    pg_vector
                ]

                if category:

                    sql += """
                        AND entity_type = %s
                    """

                    params.append(
                        category
                    )

                candidate_limit = min(
                    max(
                        limit * 10,
                        30,
                    ),
                    100,
                )

                sql += """
                    ORDER BY
                        embedding <=> %s::vector
                    LIMIT %s
                """

                params.extend(
                    [
                        pg_vector,
                        candidate_limit,
                    ]
                )

                cur.execute(
                    sql,
                    params,
                )

                semantic_rows = (
                    cur.fetchall()
                )

                # -----------------------------------------
                # 3. Búsqueda léxica
                # -----------------------------------------

                keywords = get_keywords(q)

                lexical_rows = []

                for keyword in keywords:

                    lexical_sql = """
                        SELECT
                            entity_type,
                            entity_id,
                            content,
                            1 - (
                                embedding <=> %s::vector
                            ) AS similarity
                        FROM entity_embeddings
                        WHERE embedding IS NOT NULL
                          AND content ILIKE %s
                    """

                    lexical_params = [
                        pg_vector,
                        f"%{keyword}%",
                    ]

                    if category:

                        lexical_sql += """
                            AND entity_type = %s
                        """

                        lexical_params.append(
                            category
                        )

                    lexical_sql += """
                        ORDER BY
                            embedding <=> %s::vector
                        LIMIT 20
                    """

                    lexical_params.append(
                        pg_vector
                    )

                    cur.execute(
                        lexical_sql,
                        lexical_params,
                    )

                    lexical_rows.extend(
                        cur.fetchall()
                    )

                # -----------------------------------------
                # 4. Combinar candidatos y eliminar
                #    duplicados
                # -----------------------------------------

                candidate_map = {}

                for candidate in (
                    semantic_rows
                    + lexical_rows
                ):

                    key = (
                        candidate[0],
                        candidate[1],
                    )

                    candidate_map[
                        key
                    ] = candidate

                candidates = list(
                    candidate_map.values()
                )

                results = []

                # -----------------------------------------
                # 5. Recuperar información real
                # -----------------------------------------

                for (
                    entity_type,
                    entity_id,
                    content,
                    similarity,
                ) in candidates:

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
                            (
                                entity_id,
                            ),
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
                                    "_content": content,
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
                            (
                                entity_id,
                            ),
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
                                    "_content": content,
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
                            (
                                entity_id,
                            ),
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
                                    "_content": content,
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
                            (
                                entity_id,
                            ),
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
                                    "_content": content,
                                }
                            )

        # -------------------------------------------------
        # 6. Ranking híbrido
        # -------------------------------------------------

        for item in results:

            lexical_score = keyword_score(
                q,
                item["_content"],
            )

            item[
                "keyword_score"
            ] = round(
                lexical_score,
                4,
            )

            item[
                "final_score"
            ] = round(
                (
                    item["similarity"]
                    * 0.8
                    + lexical_score
                    * 0.2
                ),
                4,
            )

        results.sort(
            key=lambda item: item[
                "final_score"
            ],
            reverse=True,
        )

        results = results[
            :limit
        ]

        # No exponer el texto usado
        # internamente para embeddings

        for item in results:

            item.pop(
                "_content",
                None,
            )

        logger.info(
            (
                "semantic_search "
                "query=%s category=%s "
                "results=%s"
            ),
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
            (
                "semantic_search_failed "
                "query=%s category=%s"
            ),
            q,
            category,
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Error interno al procesar "
                "la búsqueda semántica"
            ),
        )