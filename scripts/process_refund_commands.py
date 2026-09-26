"""Demo confirmation worker. Real money requires an explicitly integrated adapter.

Command UUID is the stable execution key. No external provider is enabled by this
script; accounting advances only after confirmation, never merely on enqueue.
"""
import argparse
import json


def process_demo(limit=100):
    from app.db import get_connection
    from app.demo.safety import verify
    from app.services.refund_execution import confirm
    with get_connection() as conn:
        verify(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM refund_commands WHERE status='pending' ORDER BY created_at,id LIMIT %s",(limit,))
            identifiers=[row[0] for row in cur.fetchall()]
    for identifier in identifiers:
        with get_connection() as conn:
            verify(conn)
            with conn.cursor() as cur: confirm(cur,identifier,'demo.refund.'+str(identifier),'demo_fake')
            conn.commit()
    return {'simulated_confirmations':len(identifiers)}


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--demo',action='store_true')
    args=parser.parse_args()
    if not args.demo: raise SystemExit('No real payment adapter configured; commands remain pending')
    from app.demo.safety import configure
    configure()
    print(json.dumps(process_demo()))


if __name__=='__main__': main()
