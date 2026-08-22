import hashlib

from sentence_transformers import SentenceTransformer

from app.db import get_connection


MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


def clean(value):
    if value is None:
        return ""
    return str(value).strip()


def content_hash(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def vector_to_pg(vector):
    return "[" + ",".join(str(float(x)) for x in vector) + "]"


def load_entities(cur):
    entities = []

    # --------------------------------------------------
    # HOTELS
    # --------------------------------------------------
    cur.execute(
        """
        SELECT
            h.id,
            h.destination_id,
            h.name,
            h.category,
            h.address,
            h.rating,
            h.price_observed,
            h.currency
        FROM hotels h
        WHERE h.status = 'active'
        """
    )

    for row in cur.fetchall():
        entity_id, destination_id, name, category, address, rating, price, currency = row

        content = (
            f"Tipo: hotel. "
            f"Nombre: {clean(name)}. "
            f"Categoría: {clean(category)}. "
            f"Dirección: {clean(address)}. "
            f"Rating: {clean(rating)}. "
            f"Precio observado: {clean(price)} {clean(currency)}."
        )

        entities.append(
            {
                "entity_type": "hotel",
                "entity_id": entity_id,
                "destination_id": destination_id,
                "content": content,
            }
        )

    # --------------------------------------------------
    # RESTAURANTS
    # --------------------------------------------------
    cur.execute(
        """
        SELECT
            r.id,
            r.destination_id,
            r.name,
            r.place_type,
            r.category,
            r.address,
            r.price_range,
            r.rating
        FROM restaurants r
        WHERE r.status = 'active'
        """
    )

    for row in cur.fetchall():
        (
            entity_id,
            destination_id,
            name,
            place_type,
            category,
            address,
            price_range,
            rating,
        ) = row

        content = (
            f"Tipo: restaurante. "
            f"Nombre: {clean(name)}. "
            f"Tipo de lugar: {clean(place_type)}. "
            f"Categoría: {clean(category)}. "
            f"Dirección: {clean(address)}. "
            f"Rango de precio: {clean(price_range)}. "
            f"Rating: {clean(rating)}."
        )

        entities.append(
            {
                "entity_type": "restaurant",
                "entity_id": entity_id,
                "destination_id": destination_id,
                "content": content,
            }
        )

    # --------------------------------------------------
    # TOURS
    # --------------------------------------------------
    cur.execute(
        """
        SELECT
            t.id,
            t.destination_id,
            t.name,
            t.description,
            t.destination_label,
            t.duration_minutes,
            t.price_from,
            t.currency,
            t.languages,
            t.meeting_point
        FROM tours t
        WHERE t.status = 'active'
        """
    )

    for row in cur.fetchall():
        (
            entity_id,
            destination_id,
            name,
            description,
            destination_label,
            duration,
            price,
            currency,
            languages,
            meeting_point,
        ) = row

        content = (
            f"Tipo: tour. "
            f"Nombre: {clean(name)}. "
            f"Descripción: {clean(description)}. "
            f"Destino: {clean(destination_label)}. "
            f"Duración: {clean(duration)} minutos. "
            f"Precio desde: {clean(price)} {clean(currency)}. "
            f"Idiomas: {clean(languages)}. "
            f"Punto de encuentro: {clean(meeting_point)}."
        )

        entities.append(
            {
                "entity_type": "tour",
                "entity_id": entity_id,
                "destination_id": destination_id,
                "content": content,
            }
        )

    # --------------------------------------------------
    # ATTRACTIONS
    # --------------------------------------------------
    cur.execute(
        """
        SELECT
            a.id,
            a.destination_id,
            a.name,
            a.category,
            a.zone,
            a.description,
            a.opening_hours,
            a.adult_price,
            a.child_price,
            a.currency
        FROM attractions a
        WHERE a.status = 'active'
        """
    )

    for row in cur.fetchall():
        (
            entity_id,
            destination_id,
            name,
            category,
            zone,
            description,
            opening_hours,
            adult_price,
            child_price,
            currency,
        ) = row

        content = (
            f"Tipo: atracción turística. "
            f"Nombre: {clean(name)}. "
            f"Categoría: {clean(category)}. "
            f"Zona: {clean(zone)}. "
            f"Descripción: {clean(description)}. "
            f"Horario: {clean(opening_hours)}. "
            f"Precio adulto: {clean(adult_price)} {clean(currency)}. "
            f"Precio niño: {clean(child_price)} {clean(currency)}."
        )

        entities.append(
            {
                "entity_type": "attraction",
                "entity_id": entity_id,
                "destination_id": destination_id,
                "content": content,
            }
        )

    return entities


def main():
    print("Conectando a PostgreSQL...")

    with get_connection() as conn:
        with conn.cursor() as cur:

            entities = load_entities(cur)

            print(f"Entidades encontradas: {len(entities)}")

            if not entities:
                print("No hay entidades para procesar.")
                return

            print(f"Cargando modelo: {MODEL_NAME}")

            model = SentenceTransformer(MODEL_NAME)

            texts = [item["content"] for item in entities]

            print("Generando embeddings...")

            embeddings = model.encode(
                texts,
                normalize_embeddings=True,
                show_progress_bar=True,
            )

            print(f"Dimensión: {embeddings.shape[1]}")

            if embeddings.shape[1] != 384:
                raise ValueError(
                    f"Se esperaban 384 dimensiones y llegaron "
                    f"{embeddings.shape[1]}"
                )

            print("Guardando embeddings en PostgreSQL...")

            for entity, embedding in zip(entities, embeddings):

                hash_value = content_hash(entity["content"])
                pg_vector = vector_to_pg(embedding)

                # Dejamos un solo embedding vigente por entidad.
                cur.execute(
                    """
                    DELETE FROM entity_embeddings
                    WHERE entity_type = %s
                      AND entity_id = %s
                    """,
                    (
                        entity["entity_type"],
                        entity["entity_id"],
                    ),
                )

                cur.execute(
                    """
                    INSERT INTO entity_embeddings (
                        destination_id,
                        entity_type,
                        entity_id,
                        content,
                        embedding,
                        embedding_model,
                        content_hash
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s::vector,
                        %s,
                        %s
                    )
                    """,
                    (
                        entity["destination_id"],
                        entity["entity_type"],
                        entity["entity_id"],
                        entity["content"],
                        pg_vector,
                        MODEL_NAME,
                        hash_value,
                    ),
                )

        conn.commit()

    print()
    print("OK - embeddings reales cargados correctamente")


if __name__ == "__main__":
    main()
