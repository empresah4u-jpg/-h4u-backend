"""Private, bounded partner reads. No account-linking or privilege mutation endpoint."""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from app.auth import Principal, get_current_actor, STAFF
from app.db import get_connection
from app.services.partner_memberships import require_partner_member, subject_uuid

router = APIRouter(prefix='/partners', tags=['Partners'])


def access(cur, actor, partner_id):
    if actor.role in STAFF:
        cur.execute('SELECT id FROM partners WHERE id=%s FOR SHARE', (partner_id,))
        if not cur.fetchone():
            raise HTTPException(404, 'Negocio no encontrado.')
        return None
    return require_partner_member(cur, actor, partner_id)


def rows(cur):
    columns = [c.name for c in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


@router.get('')
def list_partners(actor: Principal = Depends(get_current_actor),
                  limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    with get_connection() as conn, conn.cursor() as cur:
        if actor.role in STAFF:
            cur.execute('''SELECT id,code,business_name,status,reservations_enabled,suspension_source
                FROM partners ORDER BY created_at,id LIMIT %s OFFSET %s''', (limit,offset))
        elif actor.role == 'partner':
            uid = subject_uuid(actor)
            cur.execute('''SELECT 1 FROM partner_memberships m JOIN users u ON u.id=m.user_id
                WHERE m.user_id=%s AND m.status='active' AND u.role='partner' AND u.status='active' LIMIT 1''', (uid,))
            if not cur.fetchone():
                raise HTTPException(403, 'No tiene membresías activas.')
            cur.execute('''SELECT p.id,p.code,p.business_name,p.status,p.reservations_enabled,p.suspension_source,
                m.membership_role FROM partners p JOIN partner_memberships m ON m.partner_id=p.id
                JOIN users u ON u.id=m.user_id WHERE m.user_id=%s AND m.status='active'
                    AND u.role='partner' AND u.status='active'
                ORDER BY p.created_at,p.id LIMIT %s OFFSET %s''', (uid,limit,offset))
        else:
            raise HTTPException(403, 'Rol no autorizado.')
        return {'items': rows(cur), 'limit': limit, 'offset': offset}


@router.get('/{partner_id}')
def partner_profile(partner_id: UUID, actor: Principal = Depends(get_current_actor)):
    with get_connection() as conn, conn.cursor() as cur:
        membership = access(cur, actor, partner_id)
        cur.execute('''SELECT id,code,business_name,status,reservations_enabled,suspension_source,
            preferred_language FROM partners WHERE id=%s''', (partner_id,))
        return {**rows(cur)[0], 'membership': membership}


@router.get('/{partner_id}/membership')
def own_membership(partner_id: UUID, actor: Principal = Depends(get_current_actor)):
    with get_connection() as conn, conn.cursor() as cur:
        return require_partner_member(cur, actor, partner_id)


@router.get('/{partner_id}/products')
def partner_products(partner_id: UUID, actor: Principal = Depends(get_current_actor),
                     limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    with get_connection() as conn, conn.cursor() as cur:
        access(cur, actor, partner_id)
        cur.execute('''SELECT p.id,p.code,p.name,p.product_type,p.status,p.reservations_enabled,
            pp.id AS product_partner_id,pp.status AS link_status,pp.partner_price,pp.currency
            FROM product_partners pp JOIN products p ON p.id=pp.product_id
            WHERE pp.partner_id=%s ORDER BY p.name,p.id LIMIT %s OFFSET %s''', (partner_id,limit,offset))
        return {'items': rows(cur), 'limit': limit, 'offset': offset}


@router.get('/{partner_id}/requests')
def partner_requests(partner_id: UUID, actor: Principal = Depends(get_current_actor),
                     limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    with get_connection() as conn, conn.cursor() as cur:
        access(cur, actor, partner_id)
        cur.execute('''SELECT rp.id AS request_partner_id,sr.code,sr.status,sr.service_date,
            sr.preferred_time,sr.passenger_count,p.name AS product_name,rp.status AS response_status,
            rp.is_winner,rp.proposed_time,rp.proposed_price,rp.proposed_currency
            FROM request_partners rp JOIN service_requests sr ON sr.id=rp.service_request_id
            JOIN products p ON p.id=sr.product_id WHERE rp.partner_id=%s
            ORDER BY sr.created_at DESC,sr.id LIMIT %s OFFSET %s''', (partner_id,limit,offset))
        return {'items': rows(cur), 'limit': limit, 'offset': offset}


@router.get('/{partner_id}/reservations')
def partner_reservations(partner_id: UUID, actor: Principal = Depends(get_current_actor),
                         limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    with get_connection() as conn, conn.cursor() as cur:
        access(cur, actor, partner_id)
        cur.execute('''SELECT r.code,r.status,r.service_date,r.service_time,r.passenger_count,
            r.agreed_price,r.currency,p.name AS product_name FROM reservations r
            JOIN products p ON p.id=r.product_id WHERE r.partner_id=%s
            ORDER BY r.created_at DESC,r.id LIMIT %s OFFSET %s''', (partner_id,limit,offset))
        return {'items': rows(cur), 'limit': limit, 'offset': offset}
