"""Complete suite in independent disposable environments; never use persistent DEMO data."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from unittest.mock import patch
from uuid import uuid4
from scripts import setup_demo


def demo():
    name='h4u-test-demo-'+uuid4().hex
    password=secrets.token_urlsafe(40)
    admin_password=secrets.token_urlsafe(40)
    def command(args,stdin=None):
        p=subprocess.run(args,input=stdin,text=True,capture_output=True)
        if p.returncode:raise RuntimeError('Disposable demo command failed (details suppressed)')
        return p.stdout.strip()
    def sql(database,query,**kwargs):
        return command(['docker','exec','-i',name,'psql','-X','-U','postgres','-d',database,'-At','-v','ON_ERROR_STOP=1'],query)
    image=command(['docker','inspect','-f','{{.Image}}',setup_demo.CONTAINER])
    created=False
    try:
        command(['docker','run','-d','--name',name,'--label','com.h4u.test=disposable',
                 '--tmpfs','/var/lib/postgresql/data','-p','127.0.0.1::5432',
                 '-e','POSTGRES_PASSWORD='+admin_password,'-e','POSTGRES_DB=h4u_demo',image])
        created=True
        for _ in range(60):
            ready=subprocess.run(['docker','exec',name,'pg_isready','-U','postgres','-d','h4u_demo'],capture_output=True)
            if ready.returncode==0:break
            time.sleep(.5)
        else:raise RuntimeError('Disposable demo did not start')
        port=command(['docker','port',name,'5432/tcp']).rsplit(':',1)[1]
        schema=setup_demo.docker(['pg_dump','-U',setup_demo.ADMIN,'-d','h4u_demo','--schema-only','--no-owner','--no-privileges'])
        sql('h4u_demo',schema)
        sql('h4u_demo',"INSERT INTO demo_environment(singleton,purpose) VALUES (true,'h4u-persistent-demo');")
        with patch.object(setup_demo,'sql',sql):setup_demo.configure_runtime(password)
        env=os.environ.copy()
        env.update(PYTHON_DOTENV_DISABLED='true',H4U_TEST_DEMO_CONTAINER=name,
                   DEMO_DB_HOST='127.0.0.1',DEMO_DB_PORT=port,DEMO_DB_NAME='h4u_demo',
                   DEMO_DB_USER='h4u_demo_app',DEMO_DB_PASSWORD=password,
                   DB_HOST='127.0.0.1',DB_PORT=port,DB_NAME='h4u_demo',DB_USER='h4u_demo_app',DB_PASSWORD=password,
                   H4U_DEMO_MODE='true',JWT_SECRET=secrets.token_urlsafe(48),JWT_ALGORITHM='HS256',JWT_EXPIRE_MINUTES='60',
                   WHATSAPP_SEND_ENABLED='false',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
        return subprocess.run([sys.executable,'-m','pytest','-q','--tb=short','tests/test_demo.py'],env=env).returncode
    finally:
        if created:
            # Only the uniquely named, labelled container created above; no persistent volume.
            label=command(['docker','inspect','-f','{{index .Config.Labels "com.h4u.test"}}',name])
            if label!='disposable':raise RuntimeError('Refusing unsafe cleanup')
            command(['docker','rm','-f',name])
            print('Disposable demo container removed: verified',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group',choices=['all','runtime','idempotency','application','demo'],default='all')
    args=parser.parse_args()
    files=sorted(str(p) for p in Path('tests').glob('test_*.py'))
    special={'tests/test_demo.py','tests/test_request_idempotency.py','tests/test_ingestion_runtime.py'}
    groups={'runtime':['tests/test_ingestion_runtime.py'],'idempotency':['tests/test_request_idempotency.py'],
            'application':[f for f in files if f not in special],'demo':[]}
    result=0
    for name,tests in groups.items():
        if args.group not in ('all',name):continue
        print('Isolated group: '+name,flush=True)
        code=demo() if name=='demo' else subprocess.run([sys.executable,'-m','scripts.test_ingestion_isolated','-q','--tb=short',*tests]).returncode
        result=max(result,code)
    return result


if __name__=='__main__':raise SystemExit(main())
