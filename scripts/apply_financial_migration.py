"""Apply only reviewed additive migration 002, atomically and with checksum tracking."""
from hashlib import sha256
from pathlib import Path
from app.db import get_connection

MIGRATION = Path(__file__).resolve().parents[1] / 'db/migrations/002_financial_adjustments.sql'


def main():
    source = MIGRATION.read_text()
    checksum = sha256(source.encode()).hexdigest()
    with get_connection() as conn:
        conn.execute("SET LOCAL lock_timeout = '5s'")
        conn.execute("SET LOCAL statement_timeout = '30s'")
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('h4u-schema-migrations'))")
        conn.execute('''CREATE TABLE IF NOT EXISTS schema_migrations (
            version varchar(100) PRIMARY KEY, checksum char(64) NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT now())''')
        previous=conn.execute('SELECT checksum FROM schema_migrations WHERE version=%s',(MIGRATION.name,)).fetchone()
        if previous:
            if previous[0]!=checksum:
                raise RuntimeError('Applied migration checksum differs; refusing to modify schema')
            print('Migration already applied; checksum verified')
            return
        conn.execute(source)
        conn.execute('INSERT INTO schema_migrations(version,checksum) VALUES (%s,%s)',(MIGRATION.name,checksum))
    print('Migration 002 applied atomically; existing financial records preserved')


if __name__=='__main__':
    main()
