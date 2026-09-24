"""Fixed administrative capabilities, separate from partner membership roles."""
from fastapi import Depends, HTTPException
from app.auth import Principal, get_current_actor

CAPABILITIES = {
    'me': frozenset({'admin','operator'}),
    'operations.read': frozenset({'admin','operator'}),
    'users.read': frozenset({'admin'}),
    'members.read': frozenset({'admin'}),
    'manage': frozenset({'admin'}),
}


def require_capability(name):
    def check(actor: Principal=Depends(get_current_actor)):
        if actor.role not in CAPABILITIES[name]:
            raise HTTPException(403, 'Capacidad administrativa no autorizada.')
        return actor
    return Depends(check)
