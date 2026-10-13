"""Actual frontend adapters -> FastAPI -> disposable PostgreSQL journey."""
import json
import secrets
import subprocess
from datetime import date,timedelta
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import psycopg
import pytest
from app.services.passwords import hash_password
from tests.test_commercial_e2e import e2e  # noqa: F401


def test_integral_frontend_commercial_journey(e2e):
    c=e2e;db=c['db']
    original_code=db.execute('SELECT code FROM destinations WHERE id=%s',(c['destination'],)).fetchone()[0]
    db.execute("UPDATE destinations SET code='PARACAS' WHERE id=%s",(c['destination'],))
    db.execute("INSERT INTO experience_products(destination_id,experience_key,product_id,status) VALUES (%s,'ballestas',%s,'active')",(c['destination'],c['product']))
    password=secrets.token_urlsafe(32)
    db.execute('UPDATE users SET password_hash=%s WHERE id=%s',(hash_password(password),c['users']['tourist']['id']))
    class Bridge(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def forward(self):
            body=self.rfile.read(int(self.headers.get('Content-Length',0)))
            if self.path=='/__synthetic_provider_responses' and self.command=='POST':
                # Test-only loopback fixture action, never registered in FastAPI.
                candidates=db.execute('SELECT rp.id FROM request_partners rp JOIN service_requests sr ON sr.id=rp.service_request_id WHERE sr.product_id=%s AND rp.partner_id=%s',(c['product'],c['partners']['A'])).fetchall()
                assert len(candidates)==1
                response=c['client'].post('/partner-responses',headers=c['headers']['A'],json=dict(request_partner_id=str(candidates[0][0]),action='counter_offer',proposed_price='90.25',proposed_currency='PEN',proposed_time='10:00',partner_message='Synthetic E2E conditions'))
            else:
                headers={name:self.headers[name] for name in ('Authorization','Content-Type','Idempotency-Key') if self.headers.get(name)}
                response=c['client'].request(self.command,self.path,content=body,headers=headers)
            self.send_response(response.status_code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(response.content)));self.end_headers();self.wfile.write(response.content)
        do_GET=forward
        do_POST=forward
    server=ThreadingHTTPServer(('127.0.0.1',0),Bridge);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        frontend=Path(__file__).resolve().parents[2]/'h4u-web'
        process=subprocess.run(['node','--import','./tests/register-ts.mjs','tests/commercial-journey.integration.mjs'],cwd=frontend,text=True,capture_output=True,timeout=90,input=json.dumps({'upstream':'http://127.0.0.1:'+str(server.server_port),'email':c['users']['tourist']['email'],'password':password,'date':str(date.today()+timedelta(days=7)),'product':str(c['product'])}))
        # Do not echo subprocess diagnostics containing ephemeral credentials on failure.
        assert process.returncode==0,'Synthetic frontend journey failed; inspect sanitized step markers: '+process.stdout
        result=json.loads(process.stdout);assert result['checks']==15
        rid=db.execute('SELECT id FROM service_requests WHERE product_id=%s',(c['product'],)).fetchone()[0]
        assert db.execute('SELECT count(*) FROM service_requests WHERE product_id=%s',(c['product'],)).fetchone()==(1,)
        assert db.execute('SELECT count(*) FROM request_partners WHERE service_request_id=%s AND is_winner',(rid,)).fetchone()==(1,)
        assert db.execute('SELECT count(*) FROM request_offer_consents WHERE service_request_id=%s',(rid,)).fetchone()==(1,)
        assert db.execute('SELECT count(*) FROM reservations WHERE service_request_id=%s',(rid,)).fetchone()==(1,)
        assert db.execute('SELECT count(*) FROM payments p JOIN reservations r ON r.id=p.reservation_id WHERE r.service_request_id=%s',(rid,)).fetchone()==(0,)
        with pytest.raises(psycopg.errors.CheckViolation):db.execute('DELETE FROM request_offer_consents WHERE service_request_id=%s',(rid,))
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)
        db.execute('UPDATE destinations SET code=%s WHERE id=%s',(original_code,c['destination']))
