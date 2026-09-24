"""Explicit bounded worker, never started by the API. No credentials in output."""
import argparse
import json
import os
from app.messaging.provider import Settings,MetaProvider
from app.messaging.service import materialize,process_one


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--limit',type=int,default=100)
    args=parser.parse_args()
    if not 1<=args.limit<=1000: parser.error('limit must be 1..1000')
    if os.getenv('WHATSAPP_SEND_ENABLED')!='true':
        print('WhatsApp sending disabled'); return
    try:
        settings=Settings.from_env(); provider=MetaProvider(settings)
        templates=json.loads(os.getenv('WHATSAPP_TEMPLATES_JSON','{}'))
        if not isinstance(templates,dict): raise ValueError()
    except (ValueError,TypeError):
        raise SystemExit('WhatsApp configuration invalid; no sends performed') from None
    try:
        queued=materialize(templates,settings.account_id,args.limit)
        processed=0
        for _ in range(args.limit):
            if not process_one(provider,settings.account_id): break
            processed+=1
    except Exception:
        # A failed acknowledgement remains processing for quarantine/reconciliation.
        # Do not print PostgreSQL details that could include message content.
        raise SystemExit('WhatsApp worker failed; inspect aggregate diagnostics before retrying') from None
    print('queued:',queued,'processed:',processed)


if __name__=='__main__': main()
