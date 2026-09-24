"""Read-only aggregate audit; never selects addresses, message text or credentials."""
import json
from hashlib import sha256
from pathlib import Path
from app.db import get_connection


def audit(conn):
    checks={
      'orphan_inbound':'SELECT count(*) FROM inbound_messages m LEFT JOIN channel_threads t ON t.id=m.thread_id WHERE t.id IS NULL',
      'orphan_outbox':'SELECT count(*) FROM message_outbox m LEFT JOIN channel_threads t ON t.id=m.thread_id WHERE t.id IS NULL',
      'orphan_binding_actor':"SELECT count(*) FROM channel_binding_events e LEFT JOIN users u ON u.id=e.actor_id WHERE u.id IS NULL",
      'missing_capture_triggers':"SELECT 5-count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname IN ('messaging_request','messaging_candidate','messaging_reservation','messaging_payment','messaging_simulation') AND tgenabled='O'",
      'unbound_authority':"SELECT count(*) FROM channel_threads WHERE (partner_id IS NOT NULL OR traveler_id IS NOT NULL) AND bound_by IS NULL",
      'invalid_attempts':"SELECT count(*) FROM message_outbox WHERE attempt_count>5 OR attempt_count<0",
      'delivered_without_id':"SELECT count(*) FROM message_outbox WHERE status IN ('sent','delivered','read') AND provider_message_id IS NULL",
      'simulation_notifications':"SELECT count(*) FROM notification_events n JOIN service_requests s ON s.id=n.service_request_id WHERE s.messaging_suppressed",
      'stale_processing':"SELECT count(*) FROM message_outbox WHERE status='processing' AND processing_at<clock_timestamp()-interval '5 minutes'",
    }
    result={key:conn.execute(sql).fetchone()[0] for key,sql in checks.items()}
    name='007_whatsapp_messaging.sql'
    result['checksum_mismatch']=int(conn.execute('SELECT checksum FROM schema_migrations WHERE version=%s',(name,)).fetchone()!=(sha256((Path('db/migrations')/name).read_bytes()).hexdigest(),))
    return result


if __name__=='__main__':
    with get_connection() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        result=audit(conn)
    print(json.dumps(result,indent=2))
    raise SystemExit(int(any(result.values())))
