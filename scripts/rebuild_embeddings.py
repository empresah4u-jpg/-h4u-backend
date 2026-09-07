from sentence_transformers import SentenceTransformer

from app.db import get_connection


MODEL_NAME = (
    "sentence-transformers/"
    "paraphrase-multilingual-MiniLM-L12-v2"
)

model = SentenceTransformer(MODEL_NAME)


def vector_to_pg(vector):
    return "[" + ",".join(str(float(x)) for x in vector) + "]"


def rebuild_embeddings():
    with get_connection() as conn:
        with conn.cursor() as cur:
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
                    SET embedding = %s::vector
                    WHERE id = %s
                    """,
                    (pg_vector, embedding_id),
                )

                if index % 25 == 0 or index == len(rows):
                    print(
                        f"Procesados: {index}/{len(rows)}"
                    )

        conn.commit()

    print("Embeddings regenerados correctamente.")


if __name__ == "__main__":
    rebuild_embeddings()