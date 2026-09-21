"""Revalidate existing admission rules when an old candidate is used."""
from fastapi import HTTPException


def require_bookable_candidate(cur, candidate_id):
    # Lock the partner first, consistently with settlement/refund operations.
    cur.execute('''SELECT p.status,p.reservations_enabled
        FROM partners p JOIN request_partners rp ON rp.partner_id=p.id
        WHERE rp.id=%s FOR SHARE OF p''', (candidate_id,))
    partner = cur.fetchone()
    if not partner or partner[0] != 'active' or not partner[1]:
        raise HTTPException(409, 'El partner no está habilitado para nuevas reservas.')
    cur.execute('''SELECT pp.status,p.status,p.reservations_enabled
        FROM request_partners rp JOIN service_requests sr ON sr.id=rp.service_request_id
        JOIN product_partners pp ON pp.id=rp.product_partner_id
            AND pp.partner_id=rp.partner_id AND pp.product_id=sr.product_id
        JOIN products p ON p.id=sr.product_id
        WHERE rp.id=%s FOR SHARE OF pp,p''', (candidate_id,))
    product = cur.fetchone()
    if not product or product[0] != 'active' or product[1] != 'active' or not product[2]:
        raise HTTPException(409, 'El producto o su vínculo comercial no admite reservas.')
