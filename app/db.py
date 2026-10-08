import os
import psycopg
from pathlib import Path
from dotenv import load_dotenv, dotenv_values

load_dotenv()

def _connect(*, runtime=True):
    demo = os.getenv("H4U_DEMO_MODE") == "true"
    if demo != (os.getenv("DB_NAME") == "h4u_demo"):
        raise RuntimeError("Demo mode and database must match")
    if demo and os.getenv("DB_USER") != "h4u_demo_app":
        raise RuntimeError("Demo requires the restricted runtime role")
    user, password = os.getenv("DB_USER"), os.getenv("DB_PASSWORD")
    protected = os.getenv("DB_NAME") == "h4u"
    if protected and not runtime and user != 'h4u':
        raise RuntimeError('H4U migration requires explicit administrative identity')
    if protected and runtime:
        private = Path(__file__).resolve().parents[1] / '.env.runtime'
        values = dotenv_values(private, interpolate=False) if private.exists() else {}
        user = os.getenv('DB_RUNTIME_USER') or values.get('DB_RUNTIME_USER')
        password = os.getenv('DB_RUNTIME_PASSWORD') or values.get('DB_RUNTIME_PASSWORD')
        if user != 'h4u_runtime' or not password:
            raise RuntimeError('H4U requires explicit restricted runtime credentials')
    if demo and not runtime:
        raise RuntimeError('Use the official isolated demo migration runner')
    conn = psycopg.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"),
        user=user,
        password=password,
        connect_timeout=5,
    )

    if protected and runtime:
        try:
            flags = conn.execute("SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls,rolreplication FROM pg_roles WHERE rolname=current_user").fetchone()
            if not flags or any(flags):
                raise RuntimeError('Privileged role cannot serve H4U runtime')
            conn.commit()
        except Exception:
            conn.close()
            raise
    if demo:
        from app.demo.safety import verify
        try:
            verify(conn)
            conn.commit()
        except Exception:
            conn.close()
            raise
    return conn


def get_connection():
    return _connect(runtime=True)


def get_admin_connection():
    """Explicit administrative channel for migration CLIs, never FastAPI."""
    return _connect(runtime=False)
