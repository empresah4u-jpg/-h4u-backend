"""Persistent fake transport. No HTTP client, DNS, credentials or real destinations."""
from uuid import UUID
from app.db import get_connection
from app.demo.safety import verify


class FakeProvider:
    def _send(self, correlation_id):
        key=UUID(correlation_id)
        with get_connection() as conn:
            verify(conn)
            mid='demo.'+str(key)
            conn.execute('''INSERT INTO demo_message_receipts(message_key,provider_message_id)
                VALUES (%s,%s) ON CONFLICT(message_key) DO NOTHING''',(key,mid))
        return mid

    def send_text(self, recipient, text, correlation_id):
        return self._send(correlation_id)

    def send_template(self, recipient, name, language, correlation_id):
        return self._send(correlation_id)
