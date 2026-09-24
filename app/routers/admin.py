"""Administrative API: explicit capabilities, bounded lists, safe projections."""
from typing import Literal, Optional
from uuid import UUID
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from psycopg import sql
from app.db import get_connection
from app.auth import Principal
from app.admin_policy import require_capability, CAPABILITIES
from app.services import administration as service
from app.services.partner_memberships import set_membership, set_partner_state
from app.services.email_validation import normalize_email

router=APIRouter(prefix='/admin',tags=['H4U Admin'])


class Change(BaseModel):
    model_config=ConfigDict(extra='forbid')
    reason: str=Field(min_length=3,max_length=500)
    @field_validator('*',mode='before')
    @classmethod
    def trim_safe_strings(cls,value,info):
        return value.strip() if isinstance(value,str) and info.field_name!='password' else value


class UserCreate(Change):
    email: str=Field(min_length=3,max_length=320)
    password: SecretStr=Field(min_length=1,max_length=1024)
    role: Literal['operator','partner']
    partner_id: Optional[UUID]=None
    membership_role: Literal['owner','manager','staff']='staff'
    @field_validator('email')
    @classmethod
    def email_valid(cls,value): return normalize_email(value)
    @model_validator(mode='after')
    def valid_link(self):
        if (self.role=='partner')!=(self.partner_id is not None):
            raise ValueError('Usuario partner requiere negocio; operator no acepta vínculo.')
        if self.role=='operator' and 'membership_role' in self.model_fields_set:
            raise ValueError('Operator no admite rol de membresía.')
        return self


class UserState(Change):
    status: Literal['active','disabled','locked']


class MemberChange(Change):
    user_id: UUID
    membership_role: Literal['owner','manager','staff']
    status: Literal['active','suspended','revoked']='active'


class PartnerState(Change):
    status: Literal['active','pending','suspended','inactive']
    reservations_enabled: bool


class PartnerCreate(Change):
    code: str=Field(min_length=1,max_length=30,pattern=r'^[A-Za-z0-9_-]+$')
    business_name: str=Field(min_length=1,max_length=200)


class PartnerProfile(Change):
    business_name: Optional[str]=Field(default=None,min_length=1,max_length=200)
    legal_name: Optional[str]=Field(default=None,max_length=200)
    contact_name: Optional[str]=Field(default=None,max_length=150)
    phone: Optional[str]=Field(default=None,max_length=50)
    whatsapp: Optional[str]=Field(default=None,max_length=50)
    email: Optional[str]=Field(default=None,max_length=200)
    preferred_language: Optional[str]=Field(default=None,min_length=2,max_length=10)
    @field_validator('business_name')
    @classmethod
    def name_not_null(cls,value):
        if value is None: raise ValueError('Nombre requerido.')
        return value
    @field_validator('email')
    @classmethod
    def email_valid(cls,value): return normalize_email(value) if value else value


def records(cur):
    names=[c.name for c in cur.description]
    return [dict(zip(names,row)) for row in cur.fetchall()]


def listing(table,columns,limit,offset,filters=(),search=None):
    # All identifiers are server constants; values are always bound parameters.
    clauses=[]; params=[]
    for field,value in filters:
        if value is not None:
            clauses.append(sql.SQL('{}=%s').format(sql.Identifier(field))); params.append(value)
    if search:
        field,value=search
        clauses.append(sql.SQL('{} ILIKE %s').format(sql.Identifier(field))); params.append('%'+value+'%')
    where=sql.SQL(' WHERE ')+sql.SQL(' AND ').join(clauses) if clauses else sql.SQL('')
    query=sql.SQL('SELECT {} FROM {}{} ORDER BY created_at DESC,id LIMIT %s OFFSET %s').format(
        sql.SQL(',').join(map(sql.Identifier,columns)),sql.Identifier(table),where)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(query,params+[limit,offset])
        return {'items':records(cur),'limit':limit,'offset':offset}


USER_FIELDS=('id','email','role','status','traveler_id','partner_id','created_at','updated_at')
PARTNER_FIELDS=('id','code','business_name','status','suspension_source','reservations_enabled','preferred_language','created_at','updated_at')


@router.get('/me')
def me(actor: Principal=require_capability('me')):
    return {'actor':actor,'capabilities':sorted(k for k,v in CAPABILITIES.items() if actor.role in v)}


@router.get('/users')
def users(actor: Principal=require_capability('users.read'),limit: int=Query(50,ge=1,le=100),offset: int=Query(0,ge=0),
          role: Optional[Literal['admin','operator','partner','tourist']]=None,
          status: Optional[Literal['active','disabled','locked']]=None,email: Optional[str]=Query(None,max_length=320)):
    return listing('users',USER_FIELDS,limit,offset,(('role',role),('status',status)),('email',email) if email else None)


@router.get('/users/{user_id}')
def user_detail(user_id: UUID,actor: Principal=require_capability('users.read')):
    result=listing('users',USER_FIELDS,1,0,(('id',user_id),))['items']
    if not result: raise HTTPException(404,'Usuario no encontrado.')
    return result[0]


@router.post('/users',status_code=201)
def provision(payload: UserCreate,actor: Principal=require_capability('manage')):
    return service.create_user(actor,payload.email,payload.password.get_secret_value(),payload.role,
        payload.partner_id,payload.membership_role,payload.reason)


@router.patch('/users/{user_id}/status')
def user_status(user_id: UUID,payload: UserState,actor: Principal=require_capability('manage')):
    return service.set_user_status(actor,user_id,payload.status,payload.reason)


@router.get('/partners')
def partners(actor: Principal=require_capability('operations.read'),limit: int=Query(50,ge=1,le=100),offset: int=Query(0,ge=0),
             status: Optional[Literal['active','pending','suspended','inactive']]=None,search: Optional[str]=Query(None,max_length=200)):
    return listing('partners',PARTNER_FIELDS,limit,offset,(('status',status),),('business_name',search) if search else None)


@router.get('/partners/{partner_id}')
def partner_detail(partner_id: UUID,actor: Principal=require_capability('operations.read')):
    result=listing('partners',PARTNER_FIELDS,1,0,(('id',partner_id),))['items']
    if not result: raise HTTPException(404,'Negocio no encontrado.')
    return result[0]


@router.post('/partners',status_code=201)
def partner_create(payload: PartnerCreate,actor: Principal=require_capability('manage')):
    return service.create_partner(actor,payload.code,payload.business_name,payload.reason)


@router.patch('/partners/{partner_id}')
def partner_update(partner_id: UUID,payload: PartnerProfile,actor: Principal=require_capability('manage')):
    return service.update_partner_profile(actor,partner_id,payload.model_dump(exclude_unset=True,exclude={'reason'}),payload.reason)


@router.patch('/partners/{partner_id}/status')
def partner_status(partner_id: UUID,payload: PartnerState,actor: Principal=require_capability('manage')):
    return set_partner_state(actor,partner_id,payload.status,payload.reservations_enabled,payload.reason)


@router.get('/partners/{partner_id}/members')
def members(partner_id: UUID,actor: Principal=require_capability('members.read'),limit: int=Query(50,ge=1,le=100),offset: int=Query(0,ge=0)):
    partner_detail(partner_id,actor)
    return listing('partner_memberships',('id','partner_id','user_id','membership_role','status','created_at','updated_at'),limit,offset,(('partner_id',partner_id),))


@router.put('/partners/{partner_id}/members')
def member_set(partner_id: UUID,payload: MemberChange,actor: Principal=require_capability('manage')):
    return set_membership(actor,payload.user_id,partner_id,payload.membership_role,payload.status,payload.reason)


@router.get('/reservations')
def reservations(actor: Principal=require_capability('operations.read'),limit: int=Query(50,ge=1,le=100),offset: int=Query(0,ge=0),
                 partner_id: Optional[UUID]=None,status: Optional[str]=Query(None,max_length=30)):
    return listing('reservations',('id','code','partner_id','product_id','status','service_date','passenger_count','created_at'),limit,offset,(('partner_id',partner_id),('status',status)))


@router.get('/service-requests')
def requests(actor: Principal=require_capability('operations.read'),limit: int=Query(50,ge=1,le=100),offset: int=Query(0,ge=0),
             partner_id: Optional[UUID]=None,status: Optional[str]=Query(None,max_length=30)):
    return listing('service_requests',('id','code','product_id','assigned_partner_id','status','service_date','passenger_count','created_at'),limit,offset,(('assigned_partner_id',partner_id),('status',status)))
