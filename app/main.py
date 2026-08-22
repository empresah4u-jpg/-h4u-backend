from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import get_connection

from app.routers.hotels import router as hotels_router
from app.routers.restaurants import router as restaurants_router
from app.routers.tours import router as tours_router
from app.routers.attractions import router as attractions_router
from app.routers.transport import router as transport_router
from app.routers.search import router as search_router
from app.routers.semantic_search import router as semantic_search_router


app = FastAPI(
    title="H4U API",
    version="0.3.0"
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(hotels_router)
app.include_router(restaurants_router)
app.include_router(tours_router)
app.include_router(attractions_router)
app.include_router(transport_router)
app.include_router(search_router)
app.include_router(semantic_search_router)


@app.get("/health", tags=["System"])
def health():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            result = cur.fetchone()

    return {
        "status": "ok",
        "database": result[0] == 1,
    }


@app.get("/destinations", tags=["Destinations"])
def destinations():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    code,
                    name,
                    slug,
                    country_code,
                    region,
                    province,
                    district,
                    timezone,
                    currency,
                    status
                FROM destinations
                ORDER BY name
                """
            )

            rows = cur.fetchall()

    return [
        {
            "code": row[0],
            "name": row[1],
            "slug": row[2],
            "country_code": row[3],
            "region": row[4],
            "province": row[5],
            "district": row[6],
            "timezone": row[7],
            "currency": row[8],
            "status": row[9],
        }
        for row in rows
    ]
