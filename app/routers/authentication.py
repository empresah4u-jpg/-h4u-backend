"""Access-token authentication; identities are provisioned only through trusted administration."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.auth import Principal, bearer, get_current_actor
from app.services.authentication import AuthenticationService, InvalidCredentials, LoginLimited
from app.services.email_validation import normalize_email

router = APIRouter(prefix='/auth', tags=['Authentication'])


class LoginPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    email: str = Field(min_length=3, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=1024)

    @field_validator('email')
    @classmethod
    def normalize_email(cls, value):
        return normalize_email(value)

    @field_validator('password')
    @classmethod
    def password_size(cls, value):
        if len(value.get_secret_value().encode('utf-8')) > 1024:
            raise ValueError('Contraseña demasiado larga.')
        return value


class TokenResponse(BaseModel):
    access_token: str = Field(repr=False)
    token_type: Literal['bearer']
    expires_in: int


def auth_service(request: Request) -> AuthenticationService:
    service = getattr(request.app.state, 'auth_service', None)
    if service is None:
        raise HTTPException(503, 'Autenticación no disponible.')
    return service


def unauthorized():
    return HTTPException(401, 'Credenciales no válidas.', headers={'WWW-Authenticate': 'Bearer'})


@router.post('/login', response_model=TokenResponse)
def login(payload: LoginPayload, request: Request, service: AuthenticationService = Depends(auth_service)):
    client_address = request.client.host if request.client else 'unknown'
    try:
        return service.login(payload.email, payload.password.get_secret_value(), client_address)
    except InvalidCredentials:
        raise unauthorized() from None
    except LoginLimited:
        raise HTTPException(429, 'Demasiados intentos. Intente más tarde.', headers={'Retry-After': '600'}) from None


@router.get('/me', response_model=Principal)
def me(actor: Principal = Depends(get_current_actor)):
    return actor


@router.post('/logout', status_code=204)
def logout(actor: Principal = Depends(get_current_actor),
           credentials: HTTPAuthorizationCredentials = Depends(bearer),
           service: AuthenticationService = Depends(auth_service)):
    try:
        service.logout(credentials.credentials)
    except InvalidCredentials:
        raise unauthorized() from None
    return Response(status_code=204)


@router.post('/logout-all', status_code=204)
def logout_all(actor: Principal = Depends(get_current_actor),
               credentials: HTTPAuthorizationCredentials = Depends(bearer),
               service: AuthenticationService = Depends(auth_service)):
    try:
        service.logout(credentials.credentials, all_sessions=True)
    except InvalidCredentials:
        raise unauthorized() from None
    return Response(status_code=204)
