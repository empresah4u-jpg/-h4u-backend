"""Restricted demo runtime; never load production/admin credentials."""
import os
from pathlib import Path
import secrets
from dotenv import dotenv_values

DEMO_DB='h4u_demo'
DEMO_ROLE='h4u_demo_app'
PURPOSE='h4u-persistent-demo'


def configure():
    values=dotenv_values(Path(__file__).resolve().parents[2]/'.env.demo',interpolate=False)
    config={key:os.environ.get('DEMO_'+key,values.get('DEMO_'+key,'')) for key in ('DB_HOST','DB_PORT','DB_NAME','DB_USER','DB_PASSWORD')}
    if config['DB_NAME']!=DEMO_DB or config['DB_USER']!=DEMO_ROLE or not config['DB_PASSWORD']:
        raise RuntimeError('Restricted demo configuration missing; run administrative setup')
    for key in list(os.environ):
        if key.startswith(('DB_','JWT_','WHATSAPP_','PG','POSTGRES_')):
            os.environ.pop(key,None)
    os.environ.update(config)
    os.environ['PYTHON_DOTENV_DISABLED']='true'
    os.environ['H4U_DEMO_MODE']='true'
    os.environ['JWT_SECRET']=secrets.token_urlsafe(48)
    os.environ['JWT_ALGORITHM']='HS256'
    os.environ['JWT_EXPIRE_MINUTES']='60'
    os.environ['WHATSAPP_SEND_ENABLED']='false'


def verify(conn):
    if os.getenv('H4U_DEMO_MODE')!='true' or os.getenv('DB_NAME')!=DEMO_DB or os.getenv('DB_USER')!=DEMO_ROLE:
        raise RuntimeError('Demo requires its isolated database and restricted role')
    row=conn.execute('''SELECT current_database(),current_user,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls
        FROM pg_roles WHERE rolname=current_user''').fetchone()
    if not row or row[:2]!=(DEMO_DB,DEMO_ROLE) or any(row[2:]):
        raise RuntimeError('Unsafe demo database identity or elevated role')
    if not conn.execute("SELECT to_regclass('public.demo_environment')").fetchone()[0]:
        raise RuntimeError('Demo marker missing; run setup_demo first')
    if conn.execute('SELECT purpose FROM public.demo_environment WHERE singleton').fetchone()!=(PURPOSE,):
        raise RuntimeError('Demo marker invalid')
