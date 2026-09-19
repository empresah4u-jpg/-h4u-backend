import os
from datetime import datetime, timezone

import requests
import psycopg


API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")



DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT"),
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
}


def get_pending_services():
    query = """
        SELECT
            'emergency_services' AS source_table,
            code,
            name,
            google_place_id
        FROM emergency_services
        WHERE google_place_id IS NOT NULL
          AND (latitude IS NULL OR longitude IS NULL)

        UNION ALL

        SELECT
            'general_services' AS source_table,
            code,
            name,
            google_place_id
        FROM general_services
        WHERE google_place_id IS NOT NULL
          AND (latitude IS NULL OR longitude IS NULL)

        ORDER BY source_table, code;
    """

    with psycopg.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            return cur.fetchall()


def get_place_location(place_id: str):
    url = f"https://places.googleapis.com/v1/places/{place_id}"

    response = requests.get(
        url,
        headers={
            "X-Goog-Api-Key": API_KEY,
            "X-Goog-FieldMask": "location",
        },
        timeout=20,
    )

    if response.status_code != 200:
        return None, response.status_code, response.text

    data = response.json()
    return data.get("location"), response.status_code, None


def update_service(
    table_name: str,
    code: str,
    latitude: float,
    longitude: float,
):
    allowed_tables = {
        "emergency_services",
        "general_services",
    }

    if table_name not in allowed_tables:
        raise ValueError(f"Tabla no permitida: {table_name}")

    query = f"""
        UPDATE {table_name}
        SET
            latitude = %s,
            longitude = %s,
            verification_status = 'google_places_verified',
            confidence_score = 0.95,
            last_verified_at = %s,
            updated_at = %s
        WHERE code = %s;
    """

    now = datetime.now(timezone.utc)

    with psycopg.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(
                query,
                (
                    latitude,
                    longitude,
                    now,
                    now,
                    code,
                ),
            )
        conn.commit()


def main():
    if not API_KEY:
        raise RuntimeError(
            "No se encontró GOOGLE_MAPS_API_KEY. "
            "Ejecuta: set -a && source .env && set +a"
        )


    print("\nH4U - GOOGLE PLACES SERVICES UPDATE")
    print("PostgreSQL SÍ será actualizado.\n")

    services = get_pending_services()

    print(f"Servicios pendientes: {len(services)}")
    print()

    api_calls = 0
    success = 0
    failed = 0

    for source_table, code, name, place_id in services:
        print("=" * 80)
        print(f"TABLA: {source_table}")
        print(f"{code} - {name}")
        print(f"PLACE_ID: {place_id}")

        try:
            location, status_code, error = get_place_location(place_id)
            api_calls += 1

            if error:
                failed += 1
                print(f"ERROR HTTP {status_code}")
                print(error)
                continue

            if not location:
                failed += 1
                print("SIN COORDENADAS")
                continue

            latitude = location.get("latitude")
            longitude = location.get("longitude")

            if latitude is None or longitude is None:
                failed += 1
                print("COORDENADAS INCOMPLETAS")
                continue

            update_service(
                source_table,
                code,
                latitude,
                longitude,
            )

            print(f"latitude:  {latitude}")
            print(f"longitude: {longitude}")
            print("STATUS: ACTUALIZADO")

            success += 1

        except requests.RequestException as exc:
            failed += 1
            print(f"ERROR DE CONEXIÓN: {exc}")

        except psycopg.Error as exc:
            failed += 1
            print(f"ERROR POSTGRESQL: {exc}")


    print("\n" + "=" * 80)
    print("PROCESO TERMINADO")
    print(f"Servicios revisados: {len(services)}")
    print(f"Actualizados: {success}")
    print(f"Fallidos: {failed}")
    print(f"Llamadas realizadas a Google: {api_calls}")


if __name__ == "__main__":
    main()
