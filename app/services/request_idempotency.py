"""Durable completed attempts; transaction advisory locks work across workers."""
import hashlib
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import HTTPException


def fingerprint(payload):
    return hashlib.sha256(json.dumps(payload.model_dump(mode='json'), sort_keys=True,
                                    separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def replay(cur, traveler_id, key, digest):
    # Lock before lookup. READ COMMITTED's next statement sees the winner's commit.
    scope = json.dumps([str(traveler_id), key], separators=(',', ':'))
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', ('request-idempotency:' + scope,))
    cur.execute('SELECT request_fingerprint,response_body FROM service_request_idempotency WHERE traveler_id=%s AND idempotency_key=%s', (traveler_id, key))
    row = cur.fetchone()
    if row:
        if row[0] != digest:
            raise HTTPException(409, 'La clave ya corresponde a otra solicitud.')
        return row[1]


def validate_destination_time(payload, zone, now=None):
    try:
        destination_zone = ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise HTTPException(409, 'Zona horaria del destino no válida.') from None
    current = (now or datetime.now(timezone.utc)).astimezone(destination_zone)
    if payload.service_date < current.date():
        raise HTTPException(422, 'La fecha solicitada ya pasó en el destino.')
    if payload.preferred_time:
        if payload.preferred_time.tzinfo is not None:
            raise HTTPException(422, 'Use la hora local del destino sin offset.')
        requested = datetime.combine(payload.service_date, payload.preferred_time)
        # Reject ambiguous/nonexistent wall times rather than guessing a DST fold.
        possibilities = set()
        for fold in (0, 1):
            instant = requested.replace(tzinfo=destination_zone, fold=fold).astimezone(timezone.utc)
            if instant.astimezone(destination_zone).replace(tzinfo=None) == requested:
                possibilities.add(instant)
        if len(possibilities) != 1:
            raise HTTPException(422, 'Horario ambiguo o inexistente en el destino.')
        if next(iter(possibilities)) < current.astimezone(timezone.utc):
            raise HTTPException(422, 'El horario solicitado ya pasó en el destino.')
    elif not payload.flexible_time:
        raise HTTPException(422, 'Indique un horario o permita flexibilidad.')
