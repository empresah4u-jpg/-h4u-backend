from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from starlette.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg import Error as PsycopgError

from app.db import get_connection
from app.logger import get_logger
from app.identity import AuthConfigurationError, JWTSettings, JWTIdentityProvider
from app.services.authentication import AuthenticationService
from app.routers.authentication import router as authentication_router

from app.routers.admin import router as admin_router
from app.routers.partners import router as partners_router
from app.routers.hotels import router as hotels_router
from app.routers.restaurants import router as restaurants_router
from app.routers.tours import router as tours_router
from app.routers.tour_operators import router as tour_operators_router
from app.routers.attractions import router as attractions_router
from app.routers.transport import router as transport_router
from app.routers.services import router as services_router
from app.routers.search import router as search_router
from app.routers.semantic_search import router as semantic_search_router
from app.routers.service_requests import router as service_requests_router
from app.routers.partner_responses import router as partner_responses_router
from app.routers.reservations import router as reservations_router
from app.routers.passengers import router as passengers_router
from app.routers.payments import router as payments_router
from app.routers.refunds import router as refunds_router
from app.routers.commissions import router as commissions_router
from app.routers.settlements import router as settlements_router


logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    # Fail closed for identity while leaving public catalogs/health available.
    application.state.identity_provider = None
    application.state.auth_service = None
    try:
        settings = JWTSettings.from_env()
    except AuthConfigurationError:
        logger.error("authentication_unavailable invalid JWT configuration")
    else:
        application.state.auth_service = await run_in_threadpool(AuthenticationService, settings)
        application.state.identity_provider = JWTIdentityProvider(settings)
    try:
        yield
    finally:
        application.state.auth_service = None
        application.state.identity_provider = None


app = FastAPI(
    title="H4U API",
    version="0.3.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def auth_response_privacy(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(("/auth/", "/admin/")):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    return response


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    if request.url.path.startswith(("/auth/", "/admin/")):
        # FastAPI normally echoes invalid inputs, potentially including passwords.
        errors = [{key: error[key] for key in ("loc", "msg", "type")}
                  for error in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(PsycopgError)
async def database_error_handler(
    request: Request,
    exc: PsycopgError,
):
    if request.url.path.startswith('/admin/'):
        logger.error('admin_database_error type=%s', type(exc).__name__)
    else:
        logger.exception('database_error path=%s', request.url.path)

    return JSONResponse(
        status_code=500,
        headers={"Cache-Control": "no-store"},
        content={
            "error": "database_error",
            "detail": (
                "No se pudo procesar la solicitud "
                "en la base de datos."
            ),
        },
    )


@app.exception_handler(Exception)
async def general_error_handler(
    request: Request,
    exc: Exception,
):
    if request.url.path.startswith('/admin/'):
        logger.error('admin_internal_error type=%s', type(exc).__name__)
    else:
        logger.exception('internal_server_error path=%s', request.url.path)

    return JSONResponse(
        status_code=500,
        headers={"Cache-Control": "no-store"},
        content={
            "error": "internal_server_error",
            "detail": "Ocurrió un error interno.",
        },
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


app.include_router(authentication_router)
app.include_router(partners_router)
app.include_router(admin_router)
app.include_router(hotels_router)
app.include_router(restaurants_router)
app.include_router(tours_router)
app.include_router(tour_operators_router)
app.include_router(attractions_router)
app.include_router(transport_router)
app.include_router(services_router)
app.include_router(search_router)
app.include_router(semantic_search_router)
app.include_router(service_requests_router)
app.include_router(partner_responses_router)
app.include_router(reservations_router)
app.include_router(passengers_router)
app.include_router(payments_router)
app.include_router(refunds_router)
app.include_router(commissions_router)
app.include_router(settlements_router)

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