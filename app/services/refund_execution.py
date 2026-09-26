"""Confirmation boundary shared by manual evidence and future provider callbacks.

A queued command is not a processed refund. A real adapter must execute/reconcile
using command UUID as provider idempotency key and verify its webhook upstream.
"""
from fastapi import HTTPException
from app.services.finance import lock_payment_partner, event


def confirm(cur,identifier,reference,execution_kind='manual_confirmation'):
    if execution_kind not in {'manual_confirmation','demo_fake'}: raise ValueError('Unsupported execution kind')
    if not reference.strip(): raise HTTPException(400,'Referencia de devolución efectiva requerida.')
    if execution_kind=='demo_fake':
        import os
        cur.execute("SELECT current_database(),current_user")
        if os.getenv('H4U_DEMO_MODE')!='true' or cur.fetchone()!=('h4u_demo','h4u_demo_app'):
            raise HTTPException(403,'Proveedor fake reservado al entorno demo aislado.')
    cur.execute('SELECT p.code FROM payments p JOIN refund_commands c ON c.payment_id=p.id WHERE c.id=%s',(identifier,))
    row=cur.fetchone()
    if not row: raise HTTPException(404,'Comando no encontrado.')
    code=row[0]
    lock_payment_partner(cur,code)
    cur.execute('SELECT id FROM payments WHERE code=%s FOR UPDATE',(code,))
    cur.execute('''SELECT amount,status,refund_id,external_reference,execution_kind
        FROM refund_commands WHERE id=%s FOR UPDATE''',(identifier,))
    amount,status,refund_id,old_reference,old_kind=cur.fetchone()
    if status=='processed':
        if old_reference!=reference or old_kind!=execution_kind: raise HTTPException(409,'Confirmación incompatible con el resultado guardado.')
        cur.execute('SELECT result FROM refunds WHERE id=%s',(refund_id,))
        return cur.fetchone()[0]
    from app.routers.refunds import RefundCreate,create_refund_in_transaction
    result=create_refund_in_transaction(RefundCreate(payment_code=code,amount=amount,
        reason='Configured cancellation',idempotency_key='cancellation:'+str(identifier),
        external_reference=reference),cur,command_id=identifier)
    cur.execute("""UPDATE refund_commands SET status='processed',refund_id=%s,external_reference=%s,
        execution_kind=%s,processed_at=clock_timestamp() WHERE id=%s""",(result['id'],reference,execution_kind,identifier))
    event(cur,'refund_command',identifier,'confirmed',dict(refund_id=result['id'],execution_kind=execution_kind))
    return result
