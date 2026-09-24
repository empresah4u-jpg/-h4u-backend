"""Read-only administrative integrity audit; counts/checksums only, no credentials."""
from hashlib import sha256
import json
from app.db import get_connection
from scripts.apply_admin_audit import ROOT, NAME

CHECKS={
    'orphan_admin_actors': 'SELECT count(*) FROM admin_events e LEFT JOIN users u ON u.id=e.actor_id WHERE u.id IS NULL',
    'orphan_admin_targets': """SELECT count(*) FROM admin_events e WHERE
        (target_type='user' AND NOT EXISTS(SELECT 1 FROM users u WHERE u.id=e.target_id)) OR
        (target_type='partner' AND NOT EXISTS(SELECT 1 FROM partners p WHERE p.id=e.target_id)) OR
        (target_type='membership' AND NOT EXISTS(SELECT 1 FROM partner_memberships m WHERE m.id=e.target_id))""",
    'unexpected_event_actions': "SELECT count(*) FROM admin_events WHERE action NOT IN ('user.created','user.status','partner.created','partner.profile','partner.state','membership.set')",
    'credential_keys_in_events': "SELECT count(*) FROM admin_events WHERE new_values ?| ARRAY['password','password_hash','token','access_token','JWT_SECRET'] OR old_values ?| ARRAY['password','password_hash','token','access_token','JWT_SECRET']",
    'unrevoked_sessions_disabled_users': "SELECT count(*) FROM auth_sessions s JOIN users u ON u.id=s.user_id WHERE u.status<>'active' AND s.revoked_at IS NULL",
}


def main():
    with get_connection() as c:
        c.execute('SET TRANSACTION READ ONLY')
        c.execute("SET LOCAL statement_timeout='15s'")
        checks={k:c.execute(q).fetchone()[0] for k,q in CHECKS.items()}
        checks['missing_append_only_trigger']=int(not c.execute("SELECT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='admin_events'::regclass AND tgname='immutable_admin_events' AND tgenabled='O')").fetchone()[0])
        checks['checksum_mismatch']=int(c.execute('SELECT checksum FROM schema_migrations WHERE version=%s',(NAME,)).fetchone() != (sha256((ROOT/NAME).read_bytes()).hexdigest(),))
        # Existing active partners without provisioned users are a baseline warning,
        # not repaired or mutated by this read-only audit.
        legacy=c.execute("""SELECT count(*) FROM partners p WHERE p.status='active' AND NOT EXISTS
            (SELECT 1 FROM partner_memberships m JOIN users u ON u.id=m.user_id WHERE m.partner_id=p.id
             AND m.membership_role='owner' AND m.status='active' AND u.status='active' AND u.role='partner')""").fetchone()[0]
        counts={name:c.execute(query).fetchone()[0] for name,query in {
            'users':'SELECT count(*) FROM users','admins':"SELECT count(*) FROM users WHERE role='admin'",
            'auth_sessions':'SELECT count(*) FROM auth_sessions','admin_events':'SELECT count(*) FROM admin_events'}.items()}
    print(json.dumps({'checks':checks,'counts':counts,'legacy_active_partners_without_owner':legacy},indent=2))
    return int(any(checks.values()))


if __name__=='__main__': raise SystemExit(main())
