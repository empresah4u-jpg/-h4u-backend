"""Argon2id only. No plaintext password persistence or logging."""
from functools import lru_cache
import secrets
from threading import BoundedSemaphore

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import HTTPException

HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, type=Type.ID)
# Bound memory consumption per worker process; ingress also needs a global limit.
_SLOTS = BoundedSemaphore(4)


def _acquire():
    if not _SLOTS.acquire(blocking=False):
        raise HTTPException(503, 'Autenticación temporalmente ocupada.', headers={'Retry-After': '1'})


def hash_password(password: str) -> str:
    if not isinstance(password, str) or not 12 <= len(password) or len(password.encode('utf-8')) > 1024:
        raise ValueError('Password must contain at least 12 characters and at most 1024 UTF-8 bytes')
    _acquire()
    try:
        return HASHER.hash(password)
    finally:
        _SLOTS.release()


@lru_cache(maxsize=1)
def dummy_hash():
    return hash_password(secrets.token_urlsafe(48))


def verify_password(password: str, stored_hash: str) -> bool:
    if not isinstance(password, str) or not password or len(password.encode('utf-8')) > 1024:
        return False
    _acquire()
    try:
        try:
            return HASHER.verify(stored_hash, password)
        except VerifyMismatchError:
            return False
        except (InvalidHashError, VerificationError):
            # A corrupt legacy hash must neither crash nor provide a fast existence oracle.
            try:
                HASHER.verify(dummy_hash(), password)
            except VerifyMismatchError:
                pass
            return False
    finally:
        _SLOTS.release()
