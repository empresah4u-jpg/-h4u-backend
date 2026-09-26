"""Persistent demo CLI. Uses real Auth/services, fake WhatsApp and no real transfers."""
import argparse
import json


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--scenario',choices=['accept','counter_offer'],default='accept')
    parser.add_argument('--list',action='store_true',help='List stored summaries, without credentials or message content')
    args=parser.parse_args()
    from app.demo.safety import configure
    configure()
    from app.db import get_connection
    if args.list:
        with get_connection() as conn:
            rows=conn.execute('SELECT id,scenario,status,created_at,summary FROM demo_runs ORDER BY created_at DESC LIMIT 20').fetchall()
        print(json.dumps([dict(zip(('id','scenario','status','created_at','summary'),r)) for r in rows],default=str,indent=2))
    else:
        from app.demo.runner import run,DemoFailure
        try: result=run(args.scenario)
        except DemoFailure as exc: raise SystemExit(str(exc)) from None
        print(json.dumps(result,indent=2))


if __name__=='__main__': main()
