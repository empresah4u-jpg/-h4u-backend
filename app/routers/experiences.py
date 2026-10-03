"""Public editorial-to-commercial resolution; catalog prices are not quotes."""
from typing import List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel

from app.db import get_connection
from app.services.commercial_eligibility import (
    ELIGIBLE_REQUEST_PARTNER_SQL, product_accepts_requests,
)

router = APIRouter(prefix='/experiences', tags=['Experiences'])


class CommercialOption(BaseModel):
    product_id: UUID
    code: str
    name: str
    requestable: bool
    reason: Optional[Literal['commercial_option_unavailable', 'no_eligible_providers']]


class CommercialOptions(BaseModel):
    experience: str
    destination: str
    options: List[CommercialOption]


# Constant SQL composition only; all URL inputs remain bound parameters.
OPTIONS_SQL = f"""SELECT product.id, product.code, product.name,
    product.status, product.reservations_enabled,
    EXISTS (SELECT 1 FROM product_partners pp JOIN partners p ON p.id=pp.partner_id
        WHERE pp.product_id=product.id AND {ELIGIBLE_REQUEST_PARTNER_SQL})
    FROM experience_products ep
    JOIN products product ON product.id=ep.product_id AND product.destination_id=ep.destination_id
    WHERE ep.destination_id=%s AND ep.experience_key=%s AND ep.status='active'
      AND product.status='active'
    ORDER BY product.code, product.id"""


@router.get('/{destination}/{experience}/commercial-options', response_model=CommercialOptions)
def commercial_options(
    destination: str = Path(min_length=1, max_length=40, pattern=r'^[A-Z][A-Z0-9_-]*$'),
    experience: str = Path(min_length=1, max_length=80, pattern=r'^[a-z][a-z0-9]*(-[a-z0-9]+)*$'),
):
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        cur.execute('SELECT id,code FROM destinations WHERE code=%s', (destination,))
        target = cur.fetchone()
        if target is None:
            raise HTTPException(404, 'Destino no encontrado.')
        cur.execute(OPTIONS_SQL, (target[0], experience))
        options = []
        for pid, code, name, status, enabled, providers in cur.fetchall():
            reason = ('commercial_option_unavailable' if not product_accepts_requests(status, enabled)
                      else 'no_eligible_providers' if not providers else None)
            options.append(CommercialOption(product_id=pid, code=code, name=name,
                                            requestable=reason is None, reason=reason))
    return CommercialOptions(experience=experience, destination=target[1], options=options)
