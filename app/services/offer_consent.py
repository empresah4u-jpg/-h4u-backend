"""Consent evidence is assembled exclusively from locked server-side rows."""
from fastapi import HTTPException


def require_tourist(cur, actor):
    if actor is None or actor.role != 'tourist':
        raise HTTPException(403, 'Se requiere consentimiento del turista autenticado.')
    cur.execute("SELECT traveler_id FROM users WHERE id=%s AND role='tourist' AND status='active' FOR SHARE",(actor.subject,))
    row=cur.fetchone()
    if row is None or row[0]!=actor.traveler_id:
        raise HTTPException(403, 'Identidad de turista no vigente.')


def existing_consent(cur, candidate, actor, version):
    cur.execute('SELECT id,user_id,traveler_id,offer_version FROM request_offer_consents WHERE request_partner_id=%s',(candidate,))
    row=cur.fetchone()
    if row is None: return None
    if str(row[1])!=actor.subject or row[2]!=actor.traveler_id or row[3]!=version:
        raise HTTPException(409, 'La aceptación no coincide con el consentimiento registrado.')
    return row[0]


def record_consent(cur, candidate, actor, version):
    cur.execute('''SELECT r.traveler_id,o.offer_version,o.offer_request_revision,r.terms_revision,
        o.offer_request_snapshot IS NOT DISTINCT FROM request_consent_terms(r)
        FROM request_partners o JOIN service_requests r ON r.id=o.service_request_id WHERE o.id=%s''',(candidate,))
    row=cur.fetchone()
    if not row or row[0]!=actor.traveler_id: raise HTTPException(403,'Solicitud ajena.')
    if row[1]!=version or row[2]!=row[3] or not row[4]:
        raise HTTPException(409,'Oferta obsoleta; solicite una nueva versión.')
    cur.execute('''INSERT INTO request_offer_consents(
        service_request_id,request_partner_id,user_id,traveler_id,partner_id,product_id,
        offer_version,request_revision,amount_total,currency,service_date,service_time,
        flexible_time,passenger_count,adults_count,minors_count,conditions,request_snapshot)
        SELECT r.id,o.id,%s,r.traveler_id,o.partner_id,r.product_id,o.offer_version,r.terms_revision,
        o.proposed_price,o.proposed_currency,r.service_date,COALESCE(o.proposed_time,r.preferred_time),
        CASE WHEN o.proposed_time IS NULL THEN r.flexible_time ELSE false END,
        r.passenger_count,r.adults_count,r.minors_count,o.partner_message,request_consent_terms(r)
        FROM request_partners o JOIN service_requests r ON r.id=o.service_request_id
        WHERE o.id=%s RETURNING id''',(actor.subject,candidate))
    return cur.fetchone()[0]
