"""HTTP authorization independent of the identity provider.

No identity is accepted from role/owner headers or unsigned client claims.
Install an async IdentityProvider in app.state.identity_provider to authenticate
opaque/signed bearer credentials. With no provider, commercial HTTP access is closed.
"""
from contextvars import ContextVar
from dataclasses import dataclass
from typing import FrozenSet, Literal, Optional, Protocol
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.concurrency import run_in_threadpool

from app.db import get_connection

Role = Literal['tourist', 'partner', 'operator', 'admin']


class Principal(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    subject: str = Field(min_length=1, max_length=200)
    role: Role
    traveler_id: Optional[UUID] = None
    partner_id: Optional[UUID] = None

    @model_validator(mode='after')
    def owner_required(self):
        if self.role == 'tourist' and self.traveler_id is None:
            raise ValueError('A tourist identity requires traveler_id')
        if self.role == 'partner' and self.partner_id is None:
            raise ValueError('A partner identity requires partner_id')
        return self


class IdentityProvider(Protocol):
    async def authenticate(self, token: str) -> Optional[Principal]:
        """Verify credential, expiry, revocation and server-side role/owner mapping."""
        ...


bearer = HTTPBearer(auto_error=False)
_current_actor: ContextVar[Optional[Principal]] = ContextVar('commercial_actor', default=None)


async def get_current_actor(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
) -> Principal:
    provider = getattr(request.app.state, 'identity_provider', None)
    if credentials is None or provider is None:
        raise HTTPException(401, 'Identidad autenticada requerida.', headers={'WWW-Authenticate': 'Bearer'})
    actor = await provider.authenticate(credentials.credentials)
    if not isinstance(actor, Principal):
        raise HTTPException(401, 'Credencial no válida.', headers={'WWW-Authenticate': 'Bearer'})
    return actor


def audit_actor():
    actor = _current_actor.get()
    # Direct service calls are a trusted Python boundary, never an HTTP fallback.
    return (actor.subject, actor.role) if actor else ('internal-service', 'system')


@dataclass(frozen=True)
class Policy:
    roles: FrozenSet[str]
    resource: Optional[str] = None
    field: Optional[str] = None


STAFF = frozenset({'admin', 'operator'})
ALL = STAFF | {'tourist', 'partner'}
POLICIES = {
    'request.create': Policy(STAFF | {'tourist'}, 'session', 'session_id'),
    'response.create': Policy(STAFF | {'partner'}, 'candidate', 'request_partner_id'),
    'reservation.create': Policy(ALL, 'request', 'service_request_code'),
    'reservation.cancel': Policy(ALL, 'reservation', 'reservation_code'),
    'passengers.write': Policy(ALL, 'reservation', 'reservation_code'),
    'payment.create': Policy(ALL, 'reservation', 'reservation_code'),
    'payment.partner': Policy(STAFF | {'partner'}, 'payment', 'payment_code'),
    'payment.customer': Policy(STAFF | {'tourist'}, 'payment', 'payment_code'),
    'refund.create': Policy(STAFF),
    'commission.create': Policy(STAFF),
    'settlement.create': Policy(STAFF),
    'settlement.report': Policy(STAFF | {'partner'}, 'settlement', 'settlement_code'),
    'settlement.verify': Policy(STAFF),
    'settlement.overdue': Policy(STAFF),
}

# Constant SQL only. Result is (traveler owner, partner owner).
OWNERS = {
    'session': 'SELECT traveler_id, NULL::uuid FROM sessions WHERE id = %s',
    'candidate': 'SELECT sr.traveler_id, rp.partner_id FROM request_partners rp JOIN service_requests sr ON sr.id=rp.service_request_id WHERE rp.id=%s',
    'request': 'SELECT traveler_id, assigned_partner_id FROM service_requests WHERE code=%s',
    'reservation': 'SELECT traveler_id, partner_id FROM reservations WHERE code=%s',
    'payment': 'SELECT r.traveler_id,r.partner_id FROM payments p JOIN reservations r ON r.id=p.reservation_id WHERE p.code=%s',
    'settlement': 'SELECT NULL::uuid,partner_id FROM partner_settlements WHERE code=%s',
}


def check_owner(actor: Principal, resource: str, identifier):
    if resource in {'session', 'candidate'}:
        try:
            identifier = UUID(str(identifier))
        except (ValueError, TypeError):
            raise HTTPException(422, 'Identificador inválido.')
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(OWNERS[resource], (identifier,))
            owner = cur.fetchone()
    _assert_owner(actor, owner)


def _assert_owner(actor, owner):
    index = 0 if actor.role == 'tourist' else 1
    expected = actor.traveler_id if index == 0 else actor.partner_id
    # Do not reveal whether a foreign resource exists.
    if not owner or owner[index] is None or owner[index] != expected:
        raise HTTPException(403, 'No tiene acceso a este recurso.')


def recheck_owner(cur, resource, identifier):
    """Repeat ownership check after the handler locks its business resource.

    Prevents an ownership change between dependency resolution and mutation.
    Trusted direct service calls have no HTTP principal.
    """
    actor = _current_actor.get()
    if actor is not None and actor.role not in STAFF:
        cur.execute(OWNERS[resource], (identifier,))
        _assert_owner(actor, cur.fetchone())


def authorize(action: str):
    policy = POLICIES[action]

    async def permission(request: Request, actor: Principal = Depends(get_current_actor)):
        if actor.role not in policy.roles:
            raise HTTPException(403, 'Rol no autorizado para esta operación.')
        if actor.role not in STAFF:
            body = await request.json() if request.method == 'POST' and policy.field not in request.path_params else {}
            if not isinstance(body, dict):
                raise HTTPException(422, 'Se requiere un objeto JSON.')
            if action == 'request.create' and body.get('simulation'):
                raise HTTPException(403, 'Simulación reservada a operadores.')
            identifier = request.path_params.get(policy.field, body.get(policy.field))
            if not isinstance(identifier, str) or not identifier.strip():
                raise HTTPException(422, 'Identificador requerido.')
            await run_in_threadpool(check_owner, actor, policy.resource, identifier)
        token = _current_actor.set(actor)
        try:
            yield actor
        finally:
            _current_actor.reset(token)

    return Depends(permission)
