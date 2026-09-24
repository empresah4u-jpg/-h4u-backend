"""Bounded, provider-specific parsing; no raw webhook is stored."""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import re
from typing import Optional


@dataclass(frozen=True)
class InboundMessage:
    external_id: str
    sender: str
    recipient: str
    message_type: str
    text: Optional[str]
    timestamp: datetime


@dataclass(frozen=True)
class DeliveryStatus:
    external_id: str
    recipient: str
    status: str
    timestamp: datetime
    correlation_id: Optional[str]


def string(value, limit, pattern=None):
    if not isinstance(value,str) or not 1<=len(value)<=limit: raise ValueError('invalid_payload')
    if pattern and not re.fullmatch(pattern,value): raise ValueError('invalid_payload')
    return value


def timestamp(value):
    string(value,12,r'[0-9]+')
    try: result=datetime.fromtimestamp(int(value),timezone.utc)
    except (OverflowError,OSError,ValueError): raise ValueError('invalid_payload') from None
    if result>datetime.now(timezone.utc)+timedelta(minutes=5): raise ValueError('invalid_payload')
    return result


def normalize(payload, account):
    inbound=[]; statuses=[]
    if not isinstance(payload,dict) or payload.get('object')!='whatsapp_business_account': raise ValueError('invalid_payload')
    entries=payload.get('entry')
    if not isinstance(entries,list) or len(entries)>100: raise ValueError('invalid_payload')
    for entry in entries:
        for change in entry['changes']:
            if change.get('field')!='messages': continue
            value=change['value']
            if value['metadata']['phone_number_id']!=account: raise ValueError('wrong_account')
            for msg in value.get('messages',[]):
                kind=string(msg['type'],30)
                text=None
                if kind=='text': text=string(msg['text']['body'],4096)
                # Interactive payloads are not executable commands or bearer credentials.
                inbound.append(InboundMessage(string(msg['id'],200),string(msg['from'],20,r'[0-9]{6,20}'),account,kind,text,timestamp(msg['timestamp'])))
            for status in value.get('statuses',[]):
                state=status['status']
                if state not in {'sent','delivered','read','failed'}: continue
                correlation=status.get('biz_opaque_callback_data')
                if correlation is not None: string(correlation,100)
                statuses.append(DeliveryStatus(string(status['id'],200),string(status['recipient_id'],20,r'[0-9]{6,20}'),state,timestamp(status['timestamp']),correlation))
            if len(inbound)+len(statuses)>100: raise ValueError('too_many_events')
    return inbound,statuses


def parse_command(text):
    """Intent only. The caller must use the normal authenticated H4U API to act."""
    match=re.fullmatch(r'(ACEPTAR|RECHAZAR)\s+([0-9a-fA-F-]{36})',(text or '').strip(),re.IGNORECASE)
    if not match: return None
    from uuid import UUID
    try: candidate=UUID(match[2])
    except ValueError: return None
    return {'action':'accept' if match[1].upper()=='ACEPTAR' else 'reject','request_partner_id':str(candidate),'requires_authenticated_h4u':True}
