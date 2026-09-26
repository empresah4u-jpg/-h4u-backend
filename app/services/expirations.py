"""Persistent deadlines; no invented TTLs, no inference of cash settlement."""
from app.services.finance import event
from app.services.commercial_lifecycle import lock_reservation


def expire_reservation(cur,code):
    r=lock_reservation(cur,code)
    rid,status,_,_,_,_,_,_=r
    if status not in {'pending','awaiting_passenger_data','payment_pending'}: return False
    cur.execute('SELECT expires_at<=clock_timestamp() FROM reservations WHERE id=%s',(rid,))
    if not cur.fetchone()[0]: return False
    cur.execute('''SELECT id,status,partner_confirmed_at,customer_confirmed_at FROM payments
                   WHERE reservation_id=%s ORDER BY id FOR UPDATE''',(rid,))
    payments=cur.fetchall()
    # A report/one-sided cash confirmation is evidence requiring reconciliation.
    if any(p[1] in {'paid','partially_refunded','disputed','reported','waiting_verification'}
           or p[2] or p[3] for p in payments): return False
    cur.execute("UPDATE payments SET status='expired',updated_at=now() WHERE reservation_id=%s AND status='pending'",(rid,))
    cur.execute("UPDATE reservations SET status='expired',updated_at=now() WHERE id=%s",(rid,))
    event(cur,'reservation',rid,'expired',dict(previous_status=status,new_status='expired',reason='configured_deadline'))
    return True


def expire_request(cur,code):
    cur.execute('''SELECT p.id FROM partners p WHERE p.id IN
        (SELECT rp.partner_id FROM request_partners rp JOIN service_requests sr ON sr.id=rp.service_request_id WHERE sr.code=%s)
        ORDER BY p.id FOR UPDATE''',(code,))
    cur.fetchall()
    cur.execute('SELECT id,status,expires_at<=clock_timestamp() FROM service_requests WHERE code=%s FOR UPDATE',(code,))
    row=cur.fetchone()
    if not row or row[1] in {'confirmed','cancelled','expired'} or not row[2]: return False
    cur.execute('SELECT id FROM reservations WHERE service_request_id=%s',(row[0],))
    if cur.fetchone(): return False
    cur.execute("UPDATE service_requests SET status='expired',updated_at=now() WHERE id=%s",(row[0],))
    cur.execute("UPDATE request_partners SET status='expired',is_winner=false,updated_at=now() WHERE service_request_id=%s AND status IN ('pending','sent','delivered','viewed','counter_offered','accepted')",(row[0],))
    event(cur,'service_request',row[0],'expired',dict(previous_status=row[1],new_status='expired',reason='configured_deadline'))
    return True


def expire_offer(cur,identifier):
    cur.execute('SELECT partner_id,service_request_id FROM request_partners WHERE id=%s',(identifier,))
    owner=cur.fetchone()
    if not owner: return False
    cur.execute('SELECT id FROM partners WHERE id=%s FOR UPDATE',(owner[0],))
    cur.execute('SELECT id FROM service_requests WHERE id=%s FOR UPDATE',(owner[1],))
    cur.execute('SELECT status,expires_at<=clock_timestamp() FROM request_partners WHERE id=%s FOR UPDATE',(identifier,))
    row=cur.fetchone()
    if row[0] not in {'pending','sent','delivered','viewed','counter_offered'} or not row[1]: return False
    cur.execute("UPDATE request_partners SET status='expired',updated_at=now() WHERE id=%s",(identifier,))
    event(cur,'request_partner',identifier,'expired',dict(previous_status=row[0],new_status='expired',reason='configured_deadline'))
    return True


def expire_payment(cur,code):
    cur.execute('SELECT r.code FROM reservations r JOIN payments p ON p.reservation_id=r.id WHERE p.code=%s',(code,))
    owner=cur.fetchone()
    if not owner: return False
    lock_reservation(cur,owner[0])
    cur.execute('''SELECT id,status,expires_at<=clock_timestamp(),partner_confirmed_at,customer_confirmed_at
        FROM payments WHERE code=%s FOR UPDATE''',(code,))
    row=cur.fetchone()
    if row[1]!='pending' or not row[2] or row[3] or row[4]: return False
    cur.execute("UPDATE payments SET status='expired',updated_at=now() WHERE id=%s",(row[0],))
    event(cur,'payment',row[0],'expired',dict(previous_status='pending',new_status='expired',reason='configured_deadline'))
    return True


def process(connection_factory,limit=100):
    if not 1<=limit<=1000: raise ValueError('Batch limit must be between 1 and 1000')
    total={}
    for table,column,handler,states in (
        ('service_requests','code',expire_request,"'created','searching','offers_received','partner_assigned','awaiting_customer'"),
        ('request_partners','id',expire_offer,"'pending','sent','delivered','viewed','counter_offered'"),
        ('reservations','code',expire_reservation,"'pending','awaiting_passenger_data','payment_pending'"),
        ('payments','code',expire_payment,"'pending'")):
        with connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(f'SELECT {column} FROM {table} WHERE expires_at<=clock_timestamp() AND status IN ({states}) ORDER BY expires_at,id LIMIT %s',(limit,))
                candidates=[r[0] for r in cur.fetchall()]
        total[table]=0
        for identifier in candidates:
            with connection_factory() as conn:
                with conn.cursor() as cur:
                    total[table]+=int(handler(cur,identifier))
                conn.commit()
    return total
