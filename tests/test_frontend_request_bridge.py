"""Actual frontend adapter -> FastAPI -> disposable PostgreSQL, no real identities."""
import json
import subprocess
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from tests.test_commercial_e2e import e2e  # noqa: F401


def test_frontend_adapter_with_real_backend(e2e):
    c = e2e
    db = c['db']
    original_code = db.execute('SELECT code FROM destinations WHERE id=%s', (c['destination'],)).fetchone()[0]
    # The fixture explicitly rejects persistent DBs; PARACAS is a synthetic destination here.
    db.execute("UPDATE destinations SET code='PARACAS' WHERE id=%s", (c['destination'],))
    db.execute("INSERT INTO experience_products(destination_id,experience_key,product_id,status) VALUES (%s,'ballestas',%s,'active')", (c['destination'], c['product']))

    class Bridge(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def forward(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            headers = {name: self.headers[name] for name in ('Authorization', 'Content-Type', 'Idempotency-Key') if self.headers.get(name)}
            response = c['client'].request(self.command, self.path, content=body, headers=headers)
            self.send_response(response.status_code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)

        do_GET = forward
        do_POST = forward

    server = ThreadingHTTPServer(('127.0.0.1', 0), Bridge)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        frontend = Path(__file__).resolve().parents[2] / 'h4u-web'
        process = subprocess.run(
            ['node', '--import', './tests/register-ts.mjs', 'tests/service-requests.integration.mjs'],
            cwd=frontend, text=True, capture_output=True, timeout=60,
            input=json.dumps({
                'upstream': 'http://127.0.0.1:' + str(server.server_port),
                'token': c['headers']['tourist']['Authorization'].split(' ', 1)[1],
                'product': str(c['product']), 'foreign': str(c['users']['other']['session']),
                'date': str(date.today() + timedelta(days=7)),
            }),
        )
        assert process.returncode == 0, process.stderr
        result = json.loads(process.stdout)
        assert result['checks'] == 6
        assert db.execute('SELECT count(*) FROM service_requests WHERE product_id=%s', (c['product'],)).fetchone() == (1,)
        assert db.execute('SELECT count(*) FROM request_partners rp JOIN service_requests sr ON sr.id=rp.service_request_id WHERE sr.product_id=%s', (c['product'],)).fetchone() == (2,)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        db.execute('UPDATE destinations SET code=%s WHERE id=%s', (original_code,c['destination']))
