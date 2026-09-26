"""Meta transport. Never expose provider bodies, tokens or HTTP exception strings."""
from dataclasses import dataclass, field
import os
import re
from typing import Protocol
import httpx


@dataclass(frozen=True)
class Settings:
    account_id: str = ''
    verify_token: str = field(default='', repr=False)
    app_secret: str = field(default='', repr=False)
    access_token: str = field(default='', repr=False)
    api_version: str = ''

    @classmethod
    def from_env(cls):
        return cls(os.getenv('WHATSAPP_PHONE_NUMBER_ID',''),os.getenv('WHATSAPP_VERIFY_TOKEN',''),
                   os.getenv('WHATSAPP_APP_SECRET',''),os.getenv('WHATSAPP_ACCESS_TOKEN',''),
                   os.getenv('WHATSAPP_API_VERSION',''))


class SendError(Exception):
    def __init__(self, code, retryable=False):
        self.code=code
        self.retryable=retryable
        super().__init__(code)


class MessagingProvider(Protocol):
    def send_text(self, recipient: str, text: str, correlation_id: str) -> str: ...
    def send_template(self, recipient: str, name: str, language: str, correlation_id: str) -> str: ...


class MetaProvider:
    def __init__(self, settings, client=None):
        if os.getenv("H4U_DEMO_MODE")=="true" or os.getenv("DB_NAME")=="h4u_demo":
            raise ValueError("Meta is disabled for demo databases")
        if not (settings.access_token and re.fullmatch(r'[0-9]{1,40}',settings.account_id)
                and re.fullmatch(r'v[0-9]+\.0',settings.api_version)):
            raise ValueError('whatsapp_configuration_missing')
        self.settings=settings
        self.client=client

    def _send(self, recipient, kind, content, correlation_id):
        if os.getenv("H4U_DEMO_MODE")=="true" or os.getenv("DB_NAME")=="h4u_demo":
            raise SendError("demo_external_send_forbidden")
        payload={'messaging_product':'whatsapp','to':recipient,'type':kind,kind:content,
                 'biz_opaque_callback_data':correlation_id}
        url=f'https://graph.facebook.com/{self.settings.api_version}/{self.settings.account_id}/messages'
        try:
            kwargs={'json':payload,'headers':{'Authorization':'Bearer '+self.settings.access_token},'timeout':10}
            if self.client is None:
                with httpx.Client(follow_redirects=False) as client: response=client.post(url,**kwargs)
            else: response=self.client.post(url,**kwargs)
        except (httpx.ConnectError,httpx.ConnectTimeout):
            raise SendError('connection_unavailable',True) from None
        except httpx.HTTPError:
            # A timeout after bytes were sent cannot be safely retried without provider deduplication.
            raise SendError('delivery_unknown') from None
        if response.status_code==429: raise SendError('rate_limited',True)
        if response.status_code>=500: raise SendError('delivery_unknown')
        if response.status_code>=300: raise SendError('provider_rejected')
        try:
            mid=response.json()['messages'][0]['id']
            if not isinstance(mid,str) or not 1<=len(mid)<=200: raise ValueError()
            return mid
        except (ValueError,KeyError,IndexError,TypeError): raise SendError('delivery_unknown') from None

    def send_text(self, recipient, text, correlation_id):
        return self._send(recipient,'text',{'body':text},correlation_id)

    def send_template(self, recipient, name, language, correlation_id):
        return self._send(recipient,'template',{'name':name,'language':{'code':language}},correlation_id)
