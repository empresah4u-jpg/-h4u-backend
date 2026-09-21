"""Email normalization shared by authentication and trusted provisioning."""
import re


def normalize_email(value: str) -> str:
    if not isinstance(value, str) or not 3 <= len(value) <= 320:
        raise ValueError('Formato de email no válido.')
    value = value.strip().lower()
    if not value.isascii() or not re.fullmatch(r'[^@\s]{1,64}@[^@\s]{1,255}', value):
        raise ValueError('Formato de email no válido.')
    return value
