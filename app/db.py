import os
import psycopg
from dotenv import load_dotenv

load_dotenv()

def get_connection():
    demo = os.getenv("H4U_DEMO_MODE") == "true"
    if demo != (os.getenv("DB_NAME") == "h4u_demo"):
        raise RuntimeError("Demo mode and database must match")
    if demo and os.getenv("DB_USER") != "h4u_demo_app":
        raise RuntimeError("Demo requires the restricted runtime role")
    conn = psycopg.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        connect_timeout=5,
    )

    if demo:
        from app.demo.safety import verify
        try:
            verify(conn)
            conn.commit()
        except Exception:
            conn.close()
            raise
    return conn
