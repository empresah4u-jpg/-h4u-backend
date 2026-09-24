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
from app.services.partner_memberships import require_partner_member, MANAGER_ROLES, MEMBER_ROLES

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
ADMIN_ONLY = frozenset({'admin'})
ALL = STAFF | {'tourist', 'partner'}
POLICIES = {
    'request.create': Policy(STAFF | {'tourist'}, 'session', 'session_id'),
    'response.create': Policy(STAFF | {'partner'}, 'candidate', 'request_partner_id'),
    'reservation.create': Policy(ALL, 'request', 'service_request_code'),
    'reservation.cancel': Policy(ALL, 'reservation', 'reservation_code'),
    'passengers.write': Policy(ALL, 'reservation', 'reservation_code'),
    'payment.create': Policy(ALL, 'reservation', 'reservation_code'),
    'payment.partner': Policy(ADMIN_ONLY | {'partner'}, 'payment', 'payment_code'),
    'payment.customer': Policy(ADMIN_ONLY | {'tourist'}, 'payment', 'payment_code'),
    'refund.create': Policy(ADMIN_ONLY),
    'commission.create': Policy(ADMIN_ONLY),
    'settlement.create': Policy(ADMIN_ONLY),
    'settlement.report': Policy(ADMIN_ONLY | {'partner'}, 'settlement', 'settlement_code'),
    'settlement.verify': Policy(ADMIN_ONLY),
    'settlement.overdue': Policy(ADMIN_ONLY),
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
            _assert_resource_owner(cur, actor, resource, owner)


def _assert_resource_owner(cur, actor, resource, owner):
    if actor.role == 'partner':
        if not owner or owner[1] is None:
            raise HTTPException(403, 'No tiene acceso a este recurso.')
        require_partner_member(cur, actor, owner[1],
            roles=MANAGER_ROLES if resource == 'settlement' else MEMBER_ROLES,
            operation='settlement_report' if resource == 'settlement' else 'operate')
    elif not owner or owner[0] is None or owner[0] != actor.traveler_id:
        raise HTTPException(403, 'No tiene acceso a este recurso.')


def prelock_partner(cur, resource, identifier):
    """Partner before reservation/payment locks, matching the financial lock order."""
    actor = _current_actor.get()
    if actor is not None and actor.role == 'partner':
        cur.execute(OWNERS[resource], (identifier,))
        _assert_resource_owner(cur, actor, resource, cur.fetchone())


def recheck_owner(cur, resource, identifier):
    """Repeat ownership check after the handler locks its business resource.

    Prevents an ownership change between dependency resolution and mutation.
    Trusted direct service calls have no HTTP principal.
    """
    actor = _current_actor.get()
    if actor is not None and actor.role not in STAFF:
        cur.execute(OWNERS[resource], (identifier,))
        _assert_resource_owner(cur, actor, resource, cur.fetchone())


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
