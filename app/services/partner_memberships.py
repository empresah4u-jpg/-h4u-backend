"""Partner identity policy, separate from commercial eligibility and H4U staff roles."""
from uuid import UUID
from fastapi import HTTPException
from app.db import get_connection

MEMBER_ROLES = frozenset({'owner', 'manager', 'staff'})
MANAGER_ROLES = frozenset({'owner', 'manager'})


def subject_uuid(actor):
    try:
        return UUID(actor.subject)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(403, 'Identidad no autorizada.') from None


def require_partner_member(cur, actor, partner_id, *, roles=MEMBER_ROLES, operation='read'):
    if actor.role != 'partner':
        raise HTTPException(403, 'Se requiere membresía de partner.')
    uid = subject_uuid(actor)
    # Partner -> membership lock order matches commercial/settlement transactions.
    cur.execute('SELECT status,suspension_source,reservations_enabled FROM partners WHERE id=%s FOR SHARE', (partner_id,))
    partner = cur.fetchone()
    cur.execute('''SELECT m.id,m.membership_role,m.status FROM partner_memberships m
        JOIN users u ON u.id=m.user_id WHERE m.partner_id=%s AND m.user_id=%s
          AND u.role='partner' AND u.status='active' AND m.status='active'
        FOR SHARE OF m''', (partner_id, uid))
    membership = cur.fetchone()
    if not partner or not membership or membership[1] not in roles:
        raise HTTPException(403, 'No tiene acceso a este negocio.')
    if operation not in {'read', 'settlement_report'}:
        if partner[0] in {'pending','inactive'} or (partner[0]=='suspended' and partner[1]=='administrative'):
            raise HTTPException(403, 'El estado del negocio no permite esta operación.')
    return {'id': str(membership[0]), 'partner_id': str(partner_id), 'user_id': str(uid),
            'membership_role': membership[1], 'status': membership[2]}


def require_partner_manager(cur, actor, partner_id, **kwargs):
    return require_partner_member(cur, actor, partner_id, roles=MANAGER_ROLES, **kwargs)


def require_partner_owner(cur, actor, partner_id, **kwargs):
    return require_partner_member(cur, actor, partner_id, roles=frozenset({'owner'}), **kwargs)


def require_admin(cur, actor):
    uid = subject_uuid(actor)
    if actor.role != 'admin':
        raise HTTPException(403, 'Solo un administrador H4U puede provisionar membresías.')
    cur.execute("SELECT id FROM users WHERE id=%s AND role='admin' AND status='active' FOR SHARE", (uid,))
    if not cur.fetchone():
        raise HTTPException(403, 'Administrador no disponible.')
    cur.execute("SELECT set_config('h4u.partner_actor',%s,true)", (str(uid),))


def set_membership(actor, user_id: UUID, partner_id: UUID, membership_role: str, status: str = 'active'):
    """Trusted admin service; no public linking endpoint. Keeps the same row/history."""
    if membership_role not in MEMBER_ROLES or status not in {'active','suspended','revoked'}:
        raise HTTPException(422, 'Rol o estado de membresía inválido.')
    with get_connection() as conn, conn.cursor() as cur:
        require_admin(cur, actor)
        cur.execute("SELECT id FROM users WHERE id=%s AND role='partner' FOR SHARE", (user_id,))
        if not cur.fetchone():
            raise HTTPException(409, 'Se requiere un usuario partner existente.')
        cur.execute('SELECT id FROM partners WHERE id=%s FOR UPDATE', (partner_id,))
        if not cur.fetchone():
            raise HTTPException(404, 'Negocio no encontrado.')
        cur.execute('''INSERT INTO partner_memberships(partner_id,user_id,membership_role,status)
            VALUES (%s,%s,%s,%s) ON CONFLICT(partner_id,user_id) DO UPDATE
            SET membership_role=EXCLUDED.membership_role,status=EXCLUDED.status
            RETURNING id''', (partner_id,user_id,membership_role,status))
        return {'id': str(cur.fetchone()[0]), 'partner_id': str(partner_id),
                'user_id': str(user_id), 'membership_role': membership_role, 'status': status}


def set_partner_state(actor, partner_id: UUID, status: str, reservations_enabled: bool):
    """Future Admin adapter: a manual suspension can never be cleared by debt settlement."""
    if status not in {'pending','active','suspended','inactive'} or type(reservations_enabled) is not bool:
        raise HTTPException(422, 'Estado comercial inválido.')
    with get_connection() as conn, conn.cursor() as cur:
        require_admin(cur, actor)
        cur.execute('''UPDATE partners SET status=%s,suspension_source=%s,
            reservations_enabled=%s,updated_at=clock_timestamp() WHERE id=%s RETURNING id''',
            (status, 'administrative' if status=='suspended' else None, reservations_enabled, partner_id))
        if not cur.fetchone():
            raise HTTPException(404, 'Negocio no encontrado.')
