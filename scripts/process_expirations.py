"""Run periodically; persisted configuration supplies deadlines, never a default TTL."""
import argparse
import json


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--demo',action='store_true')
    parser.add_argument('--limit',type=int,default=100)
    args=parser.parse_args()
    if args.demo:
        from app.demo.safety import configure
        configure()
    from app.db import get_connection
    from app.services.expirations import process
    print(json.dumps(process(get_connection,args.limit)))


if __name__=='__main__': main()
