"""Transactional invariants shared by all administrative mutation services."""
from fastapi import HTTPException
from psycopg.types.json import Jsonb
from fastapi.encoders import jsonable_encoder


def governance_lock(cur):
    # All admin mutations acquire this BEFORE users/partners/membership row locks.
    # Serializes effective-owner decisions across membership AND account changes.
    cur.execute('SHOW transaction_isolation')
    if cur.fetchone()[0]!='read committed':
        raise HTTPException(409, 'La gobernanza requiere aislamiento READ COMMITTED.')
    cur.execute("SET LOCAL lock_timeout='5s'")
    cur.execute("SELECT pg_advisory_xact_lock(hashtext('h4u-admin-governance'))")


def admin_event(cur, actor, action, target_type, target_id, old, new, reason=None):
    cur.execute('''INSERT INTO admin_events(actor_id,action,target_type,target_id,old_values,new_values,reason)
        VALUES (%s,%s,%s,%s,%s,%s,%s)''', (actor.subject,action,target_type,target_id,
        Jsonb(jsonable_encoder(old)) if old is not None else None,Jsonb(jsonable_encoder(new)),reason))


def protect_owner(cur, partner_id, user_id):
    cur.execute('''SELECT 1 FROM partners p JOIN partner_memberships m ON m.partner_id=p.id
        JOIN users u ON u.id=m.user_id WHERE p.id=%s AND p.status='active'
        AND m.user_id=%s AND m.membership_role='owner' AND m.status='active' AND u.status='active' AND u.role='partner' ''', (partner_id,user_id))
    if not cur.fetchone(): return
    cur.execute('''SELECT 1 FROM partner_memberships m JOIN users u ON u.id=m.user_id
        WHERE m.partner_id=%s AND m.user_id<>%s AND m.membership_role='owner'
        AND m.status='active' AND u.status='active' AND u.role='partner' LIMIT 1''', (partner_id,user_id))
    if not cur.fetchone():
        raise HTTPException(409, 'El negocio activo debe conservar otro owner activo.')


def require_activation(cur, partner_id):
    cur.execute('''SELECT 1 FROM partner_memberships m JOIN users u ON u.id=m.user_id
        WHERE m.partner_id=%s AND m.membership_role='owner' AND m.status='active'
        AND u.status='active' AND u.role='partner' LIMIT 1''', (partner_id,))
    if not cur.fetchone():
        raise HTTPException(409, 'La activación requiere un owner activo.')
    cur.execute("""SELECT 1 FROM partner_settlements WHERE partner_id=%s AND
        (status='overdue' OR (status='pending_payment' AND due_date<CURRENT_DATE)) LIMIT 1""",(partner_id,))
    if cur.fetchone():
        raise HTTPException(409, 'La deuda vencida debe resolverse antes de activar el negocio.')
