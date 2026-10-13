"""Owned web sessions; no anonymous or authentication-session reuse."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from app.auth import Principal, get_current_actor, recheck_principal
from app.db import get_connection

router = APIRouter(prefix='/commercial-sessions', tags=['Service Requests'])


class SessionDestination(BaseModel):
    model_config = ConfigDict(extra='forbid')
    destination: str = Field(min_length=1, max_length=20, pattern=r'^[A-Z][A-Z0-9_-]*$')


@router.post('')
def resolve_session(payload: SessionDestination, actor: Principal = Depends(get_current_actor)):
    if actor.role != 'tourist' or actor.traveler_id is None:
        raise HTTPException(403, 'Se requiere un turista vinculado.')
    with get_connection() as conn:
        with conn.cursor() as cur:
            recheck_principal(cur, actor)
        # Serialize our web creation/reuse across processes without changing historical sessions.
        traveler = conn.execute('SELECT id FROM travelers WHERE id=%s FOR NO KEY UPDATE', (actor.traveler_id,)).fetchone()
        if not traveler:
            raise HTTPException(403, 'Turista no disponible.')
        destination = conn.execute("SELECT id FROM destinations WHERE code=%s AND status='active' FOR SHARE", (payload.destination,)).fetchone()
        if not destination:
            raise HTTPException(404, 'Destino no disponible.')
        row = conn.execute("""SELECT id FROM sessions WHERE traveler_id=%s AND destination_id=%s
            AND channel='web' AND status='active' AND ended_at IS NULL
            ORDER BY started_at,id LIMIT 1 FOR UPDATE""", (actor.traveler_id,destination[0])).fetchone()
        if not row:
            row = conn.execute("INSERT INTO sessions(traveler_id,destination_id,channel) VALUES (%s,%s,'web') RETURNING id", (actor.traveler_id,destination[0])).fetchone()
        return {'session_id':str(row[0]), 'destination':payload.destination}
