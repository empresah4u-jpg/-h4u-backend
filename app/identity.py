"""JWT identity with database-backed revocation; no blocking work on the event loop."""
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import jwt
from starlette.concurrency import run_in_threadpool

from app.auth import Principal
from app.db import get_connection


class AuthConfigurationError(ValueError):
    """Messages deliberately contain no configuration values."""


@dataclass(frozen=True)
class JWTSettings:
    secret: str = field(repr=False)
    algorithm: str = 'HS256'
    expire_minutes: int = 60
    issuer: str = 'h4u-auth'
    audience: str = 'h4u-api'

    def __post_init__(self):
        if not isinstance(self.secret, str) or len(self.secret.encode('utf-8')) < 32:
            raise AuthConfigurationError('JWT_SECRET must contain at least 32 bytes')
        if self.algorithm != 'HS256':
            raise AuthConfigurationError('Only HS256 is supported')
        if type(self.expire_minutes) is not int or not 1 <= self.expire_minutes <= 60:
            raise AuthConfigurationError('Access token lifetime must be between 1 and 60 minutes')

    @classmethod
    def from_env(cls):
        try:
            minutes = int(os.getenv('JWT_EXPIRE_MINUTES', '60'))
        except ValueError:
            raise AuthConfigurationError('Invalid access token lifetime') from None
        return cls(secret=os.getenv('JWT_SECRET', ''),
                   algorithm=os.getenv('JWT_ALGORITHM', 'HS256'), expire_minutes=minutes)


@dataclass(frozen=True)
class AccessClaims:
    user_id: UUID
    token_id: UUID
    version: int
    issued_at: int
    expires_at: int


def decode_access_token(token: str, settings: JWTSettings) -> Optional[AccessClaims]:
    if not isinstance(token, str) or not 1 <= len(token) <= 4096:
        return None
    try:
        header = jwt.get_unverified_header(token)
        if header.get('alg') != settings.algorithm or header.get('typ') != 'JWT':
            return None
        payload = jwt.decode(
            token, settings.secret, algorithms=[settings.algorithm],
            issuer=settings.issuer, audience=settings.audience,
            options={'require': ['sub', 'jti', 'exp', 'iat', 'ver', 'iss', 'aud', 'token_use'],
                     'strict_aud': True},
        )
        if payload['token_use'] != 'access':
            return None
        # PyJWT accepts some numeric strings: the H4U contract deliberately does not.
        if any(type(payload[name]) is not int for name in ('ver', 'iat', 'exp')):
            return None
        if not (payload['ver'] >= 0 and 0 <= payload['iat'] < payload['exp']
                and payload['exp'] - payload['iat'] <= settings.expire_minutes * 60):
            return None
        if not isinstance(payload['sub'], str) or not isinstance(payload['jti'], str):
            return None
        return AccessClaims(UUID(payload['sub']), UUID(payload['jti']), payload['ver'],
                            payload['iat'], payload['exp'])
    except (jwt.InvalidTokenError, ValueError, TypeError, KeyError, OverflowError):
        return None


def issue_access_token(user_id: UUID, token_id: UUID, version: int,
                       issued_at: datetime, expires_at: datetime, settings: JWTSettings) -> str:
    return jwt.encode({
        'sub': str(user_id), 'jti': str(token_id), 'ver': version,
        'iat': int(issued_at.timestamp()), 'exp': int(expires_at.timestamp()),
        'iss': settings.issuer, 'aud': settings.audience, 'token_use': 'access',
    }, settings.secret, algorithm=settings.algorithm, headers={'typ': 'JWT'})


class JWTIdentityProvider:
    def __init__(self, settings: Optional[JWTSettings] = None):
        self.settings = settings if settings is not None else JWTSettings.from_env()

    async def authenticate(self, token: str) -> Optional[Principal]:
        return await run_in_threadpool(self.authenticate_sync, token)

    def authenticate_sync(self, token: str) -> Optional[Principal]:
        claims = decode_access_token(token, self.settings)
        if claims is None:
            return None
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Common order with login/logout: user, then session.
                # SHARE prevents status/version/ownership changing during validation.
                cur.execute('''SELECT id,role,traveler_id,partner_id,status,token_version
                               FROM users WHERE id=%s FOR SHARE''', (claims.user_id,))
                user = cur.fetchone()
                if user is None or user[4] != 'active' or user[5] != claims.version:
                    return None
                try:
                    actor = Principal(subject=str(user[0]), role=user[1],
                                      traveler_id=user[2], partner_id=user[3])
                except ValueError:
                    return None
                cur.execute('''SELECT id FROM auth_sessions
                    WHERE user_id=%s AND token_id=%s AND revoked_at IS NULL
                      AND expires_at > clock_timestamp() FOR UPDATE''',
                            (claims.user_id, claims.token_id))
                session = cur.fetchone()
                if session is None:
                    return None
                # JWT may expire while waiting on a row lock.
                if claims.expires_at <= datetime.now(timezone.utc).timestamp():
                    return None
                cur.execute('''UPDATE auth_sessions SET last_seen_at=clock_timestamp()
                    WHERE id=%s AND revoked_at IS NULL AND expires_at>clock_timestamp()
                    RETURNING id''', (session[0],))
                if cur.fetchone() is None:
                    return None
        return actor
