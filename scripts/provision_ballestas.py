"""Explicit neutral draft provisioning. Default rolls back; --apply persists.

requires_payment=True is a conservative technical placeholder, NOT an approved
commercial policy. No price, provider, availability or activation is provisioned.
"""
import argparse
import json
import logging
from psycopg import sql

CODE = 'PROD-BALLESTAS'
FIELDS = ('destination_id','code','name','slug','product_type','source_entity_type',
          'source_entity_id','booking_mode','confirmation_mode','price_from','currency',
          'status','reservations_enabled','requires_payment','description')
HISTORY = ('tours','partners','product_partners','service_requests','reservations',
           'payments','commissions','partner_settlements','refunds','destinations')


def snapshot(conn):
    result = {}
    for table in HISTORY + ('products','experience_products'):
        where = sql.SQL('')
        params = ()
        if table == 'products':
            where = sql.SQL(' WHERE code<>%s'); params = (CODE,)
        if table == 'experience_products':
            where = sql.SQL(' WHERE product_id NOT IN (SELECT id FROM products WHERE code=%s)'); params=(CODE,)
        query = sql.SQL("SELECT count(*),md5(coalesce(string_agg(to_jsonb(x)::text,'' ORDER BY id),'')) FROM {} x{}").format(sql.Identifier(table),where)
        result[table] = conn.execute(query,params).fetchone()
    return result


def provision(conn):
    # Caller owns the transaction: any error rolls back the complete operation.
    conn.execute("SET LOCAL lock_timeout='5s'; SET LOCAL statement_timeout='30s'")
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('h4u-provision-neutral-ballestas'))")
    conn.execute('LOCK TABLE products,experience_products IN SHARE ROW EXCLUSIVE MODE')
    before = snapshot(conn)
    destination = conn.execute("SELECT id FROM destinations WHERE code='PARACAS' FOR SHARE").fetchone()
    if destination is None:
        raise RuntimeError('Destination not provisioned')
    values = (destination[0],CODE,'Salida Islas Ballestas','paracas-salida-islas-ballestas',
              'tour',None,None,'request','partner_confirmation',None,None,'draft',False,True,None)
    columns = sql.SQL(',').join(map(sql.Identifier,FIELDS))
    existing = conn.execute(sql.SQL('SELECT id,{} FROM products WHERE code=%s FOR UPDATE').format(columns),(CODE,)).fetchone()
    created = existing is None
    if existing:
        if tuple(existing[1:]) != values:
            raise RuntimeError('Existing neutral product differs; manual review required')
        pid = existing[0]
    else:
        pid = conn.execute(sql.SQL('INSERT INTO products ({}) VALUES ({}) RETURNING id').format(
            columns,sql.SQL(',').join(sql.Placeholder() for _ in values)),values).fetchone()[0]
    if conn.execute('SELECT 1 FROM product_partners WHERE product_id=%s',(pid,)).fetchone():
        raise RuntimeError('Neutral product already has providers; review required')
    links = conn.execute('SELECT id,destination_id,experience_key,status FROM experience_products WHERE product_id=%s FOR UPDATE',(pid,)).fetchall()
    if links:
        if len(links)!=1 or tuple(links[0][1:])!=(destination[0],'ballestas','draft'):
            raise RuntimeError('Existing association differs; manual review required')
        eid=links[0][0]
    else:
        eid=conn.execute("INSERT INTO experience_products(destination_id,experience_key,product_id,status) VALUES (%s,'ballestas',%s,'draft') RETURNING id",(destination[0],pid)).fetchone()[0]
    if snapshot(conn)!=before:
        raise RuntimeError('Historical integrity mismatch')
    return dict(product_id=str(pid),code=CODE,status='draft',association_id=str(eid),
                association_status='draft',created=created,providers=0,
                payment_policy='pending; conservative requires_payment=true',historical_integrity=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        from app.db import get_admin_connection as get_connection
        with get_connection() as conn:
            conn.autocommit=True
            if conn.execute('SELECT current_database()').fetchone()!=('h4u',):
                raise RuntimeError('Unexpected target')
            with conn.transaction(force_rollback=not args.apply):
                result=provision(conn)
            result['mode']='apply' if args.apply else 'dry-run rolled back'
            print(json.dumps(result))
    except Exception as exc:
        # Never dump connection strings, environment or SQL parameter values.
        print(json.dumps({'error':type(exc).__name__,'message':'Provisioning stopped; no partial transaction committed'}))
        raise SystemExit(1) from None


if __name__=='__main__': main()
