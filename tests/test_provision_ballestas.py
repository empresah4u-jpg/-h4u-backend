import pytest
from tests.test_experience_products import case, read
from scripts.provision_ballestas import provision, CODE, snapshot, HISTORY


@pytest.fixture
def storage(case):
    conn=case[0]
    # Shadow every additional table inspected by integrity checks, never copy rows.
    for table in HISTORY:
        if table not in ('destinations','partners','product_partners'):
            conn.execute(f'CREATE TEMP TABLE {table} (LIKE public.{table} INCLUDING ALL)')
    return case


def test_dry_run_rolls_back(storage):
    conn=storage[0]
    before=snapshot(conn)
    with conn.transaction(force_rollback=True): provision(conn)
    assert conn.execute('SELECT count(*) FROM products WHERE code=%s',(CODE,)).fetchone()==(0,)
    assert snapshot(conn)==before


def test_create_idempotent_private_and_preserve_history(storage):
    conn=storage[0];before=snapshot(conn)
    with conn.transaction(): first=provision(conn)
    timestamps=conn.execute('SELECT created_at,updated_at FROM products WHERE code=%s',(CODE,)).fetchone()
    with conn.transaction(): second=provision(conn)
    assert first['created'] is True and second['created'] is False
    assert first['product_id']==second['product_id'] and first['association_id']==second['association_id']
    assert timestamps==conn.execute('SELECT created_at,updated_at FROM products WHERE code=%s',(CODE,)).fetchone()
    assert snapshot(conn)==before
    assert CODE not in [o['code'] for o in read(storage).json()['options']]
    assert conn.execute('SELECT source_entity_type,source_entity_id,price_from,currency,status,reservations_enabled,requires_payment FROM products WHERE code=%s',(CODE,)).fetchone()==(None,None,None,None,'draft',False,True)


def test_mismatch_never_overwritten(storage):
    conn=storage[0]
    with conn.transaction(): provision(conn)
    conn.execute("UPDATE products SET name='Changed externally' WHERE code=%s",(CODE,))
    with pytest.raises(RuntimeError),conn.transaction(): provision(conn)
    assert conn.execute('SELECT name FROM products WHERE code=%s',(CODE,)).fetchone()==('Changed externally',)


def test_link_failure_rolls_back_product(storage):
    conn=storage[0]
    conn.execute("ALTER TABLE experience_products ADD CONSTRAINT deny_neutral CHECK (experience_key<>'ballestas') NOT VALID")
    with pytest.raises(Exception),conn.transaction(): provision(conn)
    assert conn.execute('SELECT count(*) FROM products WHERE code=%s',(CODE,)).fetchone()==(0,)


def test_provider_link_requires_review(storage):
    conn=storage[0]
    with conn.transaction(): result=provision(conn)
    conn.execute('INSERT INTO product_partners(product_id,partner_id) VALUES (%s,%s)',(result['product_id'],storage[4]))
    with pytest.raises(RuntimeError),conn.transaction(): provision(conn)
