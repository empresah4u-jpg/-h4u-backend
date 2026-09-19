import argparse
from app.db import get_connection
from app.embeddings import MODEL_NAME, get_model, vector_to_pg, check_storage


def rebuild_embeddings(apply=False):
    with get_connection() as conn:
        with conn.cursor() as cur:
            check_storage(cur)
            cur.execute(
                """
                SELECT
                    id,
                    content
                FROM entity_embeddings
                ORDER BY id
                """
            )

            rows = cur.fetchall()

            print(f"Embeddings encontrados: {len(rows)}")

            if not apply:
                print("Solo inspección; use --apply para reconstruir explícitamente.")
                return
            model = get_model()
            for index, (embedding_id, content) in enumerate(
                rows,
                start=1,
            ):
                vector = model.encode(
                    content,
                    normalize_embeddings=True,
                )

                pg_vector = vector_to_pg(vector)

                cur.execute(
                    """
                    UPDATE entity_embeddings
                    SET embedding = %s::vector, embedding_model = %s
                    WHERE id = %s
                    """,
                    (pg_vector, MODEL_NAME, embedding_id),
                )

                if index % 25 == 0 or index == len(rows):
                    print(
                        f"Procesados: {index}/{len(rows)}"
                    )

        conn.commit()

    print("Embeddings regenerados correctamente.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    rebuild_embeddings(apply=parser.parse_args().apply)