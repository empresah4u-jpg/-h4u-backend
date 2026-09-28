"""Reusable, offline input validation and conservative identity helpers."""
import math
import re
import unicodedata
from typing import Optional
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


def identity_text(value):
    return ' '.join(unicodedata.normalize('NFKC', value or '').casefold().split())


def normalize_url(value):
    """Normalize syntax only; never fetch, remove query data, or assert provenance."""
    if not isinstance(value,str) or not value.strip():
        raise ValueError('Expected HTTP(S) URL')
    value=value.strip()
    if re.search(r'[\s\\\x00-\x1f\x7f]',value):
        raise ValueError('Invalid URL characters')
    parts=urlsplit(value)
    if parts.scheme.lower() not in ('http','https') or not parts.hostname or parts.username is not None or parts.password is not None:
        raise ValueError('Expected HTTP(S) URL without credentials')
    host=parts.hostname.encode('idna').decode().lower()
    if ':' not in host and not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?',host):
        raise ValueError('Invalid hostname')
    if ':' in host: host='['+host+']'
    port=parts.port
    if port is not None and port != (443 if parts.scheme.lower()=='https' else 80):
        host+=':'+str(port)
    return urlunsplit((parts.scheme.lower(),host,parts.path or '/',parts.query,parts.fragment))


class Evidence(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,str_strip_whitespace=True)
    note: str=Field(min_length=1,max_length=4000)
    source_reference: Optional[str]=Field(default=None,min_length=1,max_length=500)
    description: Optional[str]=Field(default=None,max_length=10000)
    stars: Optional[int]=Field(default=None,ge=1,le=5)


class CatalogInput(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,str_strip_whitespace=True)
    source_id: str
    source: Optional[str]=Field(default=None,min_length=1,max_length=200)
    source_url: str
    external_id: Optional[str]=Field(default=None,min_length=1,max_length=255)
    destination: str=Field(min_length=1,max_length=100)
    name: str=Field(min_length=1,max_length=200)
    metadata: Evidence

    @field_validator('source_id')
    @classmethod
    def uuid_value(cls,value):
        return str(UUID(value))

    @field_validator('source_url')
    @classmethod
    def url_value(cls,value):
        return normalize_url(value)


def validate_record(model, raw):
    try:
        return model.model_validate(raw),[]
    except ValidationError as exc:
        # No echo of input or exception context containing private input.
        return None,[{'field':'.'.join(map(str,e['loc'])),'error':e['type']} for e in exc.errors()]


class HotelInput(CatalogInput):
    code: str=Field(min_length=1,max_length=20)
    category: Optional[str]=Field(default=None,min_length=1,max_length=100)
    address: Optional[str]=Field(default=None,min_length=1,max_length=4000)
    latitude: Optional[float]=Field(default=None,ge=-90,le=90)
    longitude: Optional[float]=Field(default=None,ge=-180,le=180)
    phone: Optional[str]=Field(default=None,min_length=1,max_length=50)
    website: Optional[str]=None
    google_place_id: Optional[str]=Field(default=None,min_length=1,max_length=255)
    zone: Optional[str]=Field(default=None,min_length=1,max_length=100)
    status: str='active'

    @field_validator('website')
    @classmethod
    def website_value(cls,value):
        return normalize_url(value) if value is not None else value

    @field_validator('phone')
    @classmethod
    def phone_value(cls,value):
        if value is not None and (not re.fullmatch(r'[+0-9(). /-]+',value) or not any(c.isdigit() for c in value)):
            raise ValueError('Invalid phone format')
        return value

    @field_validator('status')
    @classmethod
    def status_value(cls,value):
        if value not in ('active','inactive'): raise ValueError('Unsupported status')
        return value

    @model_validator(mode='after')
    def coordinates_and_nulls(self):
        for field in self.model_fields_set:
            if getattr(self,field) is None:
                raise ValueError('Explicit nulls are not supported; omit absent fields')
        if ('latitude' in self.model_fields_set)!=('longitude' in self.model_fields_set):
            raise ValueError('Coordinates require a pair')
        if self.latitude is not None and not all(math.isfinite(v) for v in (self.latitude,self.longitude)):
            raise ValueError('Coordinates must be finite')
        return self
