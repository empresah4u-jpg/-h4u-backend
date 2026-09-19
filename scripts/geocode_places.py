import os
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


def get_pending_hotels():
    query = """
        SELECT
            code,
            name,
            google_place_id
        FROM hotels
        WHERE google_place_id IS NOT NULL
          AND (latitude IS NULL OR longitude IS NULL)
        ORDER BY code;
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


def main():
    if not API_KEY:
        raise RuntimeError(
            "No se encontró GOOGLE_MAPS_API_KEY. "
            "Ejecuta: set -a && source .env && set +a"
        )


    print("\nH4U - GOOGLE PLACES HOTELS DRY RUN")
    print("PostgreSQL NO será modificado.\n")

    hotels = get_pending_hotels()

    print(f"Hoteles pendientes: {len(hotels)}")
    print()

    api_calls = 0

    for code, name, place_id in hotels:
        print("=" * 80)
        print(f"{code} - {name}")
        print(f"PLACE_ID: {place_id}")

        try:
            location, status_code, error = get_place_location(place_id)
            api_calls += 1

            if error:
                print(f"ERROR HTTP {status_code}")
                print(error)
                continue

            if not location:
                print("SIN COORDENADAS")
                continue

            print(f"latitude:  {location.get('latitude')}")
            print(f"longitude: {location.get('longitude')}")
            print("STATUS: OK")

        except requests.RequestException as exc:
            print(f"ERROR DE CONEXIÓN: {exc}")


    print("\n" + "=" * 80)
    print("DRY RUN TERMINADO")
    print(f"Hoteles revisados: {len(hotels)}")
    print(f"Llamadas realizadas a Google: {api_calls}")
    print("PostgreSQL NO fue modificado.")


if __name__ == "__main__":
    main()
