"""Explicit safe-field services; never serialize credential columns."""
from fastapi import HTTPException
from psycopg import errors, sql
from app.db import get_connection
from app.services.passwords import hash_password
from app.services.email_validation import normalize_email
from app.services.partner_memberships import require_admin, upsert_membership
from app.services.admin_governance import governance_lock, admin_event, protect_owner

PROFILE_FIELDS=frozenset({'business_name','legal_name','contact_name','phone','whatsapp','email','preferred_language'})


def create_user(actor, email, password, role, partner_id=None, membership_role='staff', reason=None):
    if role not in {'operator','partner'} or (role=='partner')!=(partner_id is not None):
        raise HTTPException(422, 'Provisión limitada a operator o partner con vínculo válido.')
    email=normalize_email(email)
    try: hashed=hash_password(password)
    except ValueError: raise HTTPException(422, 'Contraseña fuera de la política vigente.') from None
    try:
        with get_connection() as conn, conn.cursor() as cur:
            governance_lock(cur)
            require_admin(cur,actor)
            if partner_id:
                cur.execute('SELECT id FROM partners WHERE id=%s FOR UPDATE',(partner_id,))
                if not cur.fetchone(): raise HTTPException(404, 'Negocio no encontrado.')
            cur.execute("""INSERT INTO users(email,password_hash,role,partner_id,status)
                VALUES (%s,%s,%s,%s,'active') RETURNING id""",(email,hashed,role,partner_id))
            uid=cur.fetchone()[0]
            if role=='partner': upsert_membership(cur,actor,uid,partner_id,membership_role,'active',reason)
            admin_event(cur,actor,'user.created','user',uid,None,{'role':role,'status':'active','partner_id':str(partner_id) if partner_id else None},reason)
            return {'id':str(uid),'email':email,'role':role,'status':'active','partner_id':partner_id}
    except errors.UniqueViolation:
        raise HTTPException(409, 'La cuenta ya existe.') from None


def set_user_status(actor, user_id, status, reason):
    if status not in {'active','disabled','locked'}: raise HTTPException(422,'Estado inválido.')
    with get_connection() as conn, conn.cursor() as cur:
        governance_lock(cur)
        require_admin(cur,actor)
        cur.execute('SELECT role,status FROM users WHERE id=%s FOR UPDATE',(user_id,))
        old=cur.fetchone()
        if not old: raise HTTPException(404,'Usuario no encontrado.')
        # No account takeover/lockout path for any H4U administrator in this phase.
        if old[0]=='admin': raise HTTPException(403,'Las cuentas admin no se modifican mediante este endpoint.')
        if status!='active':
            cur.execute("""SELECT p.id FROM partners p JOIN partner_memberships m ON m.partner_id=p.id
                WHERE m.user_id=%s ORDER BY p.id FOR UPDATE OF p""",(user_id,))
            for row in cur.fetchall(): protect_owner(cur,row[0],user_id)
        cur.execute('UPDATE users SET status=%s,updated_at=clock_timestamp() WHERE id=%s RETURNING token_version',(status,user_id))
        version=cur.fetchone()[0]
        if old[1]!=status: admin_event(cur,actor,'user.status','user',user_id,{'status':old[1]},{'status':status},reason)
        return {'id':str(user_id),'role':old[0],'status':status,'token_version':version}


def create_partner(actor, code, business_name, reason):
    try:
        with get_connection() as conn, conn.cursor() as cur:
            governance_lock(cur)
            require_admin(cur,actor)
            cur.execute("INSERT INTO partners(code,business_name,status,reservations_enabled) VALUES (%s,%s,'pending',false) RETURNING id",(code,business_name))
            pid=cur.fetchone()[0]
            new={'code':code,'business_name':business_name,'status':'pending','reservations_enabled':False}
            admin_event(cur,actor,'partner.created','partner',pid,None,new,reason)
            return {'id':str(pid),**new}
    except errors.UniqueViolation:
        raise HTTPException(409,'Código de negocio ya registrado.') from None


def update_partner_profile(actor, partner_id, changes, reason):
    if not changes or not set(changes)<=PROFILE_FIELDS: raise HTTPException(422,'Campos de perfil no permitidos.')
    fields=sorted(changes)
    with get_connection() as conn, conn.cursor() as cur:
        governance_lock(cur)
        require_admin(cur,actor)
        cur.execute(sql.SQL('SELECT {} FROM partners WHERE id=%s FOR UPDATE').format(sql.SQL(',').join(map(sql.Identifier,fields))),(partner_id,))
        row=cur.fetchone()
        if not row: raise HTTPException(404,'Negocio no encontrado.')
        old=dict(zip(fields,row))
        cur.execute(sql.SQL('UPDATE partners SET {},updated_at=clock_timestamp() WHERE id=%s').format(
            sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(f)) for f in fields)),[changes[f] for f in fields]+[partner_id])
        if old!=changes: admin_event(cur,actor,'partner.profile','partner',partner_id,old,changes,reason)
        return {'id':str(partner_id),**changes}
