-- Administrative history is separate from partner and financial ledgers.
CREATE TABLE admin_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_id uuid NOT NULL REFERENCES users(id),
    action varchar(60) NOT NULL,
    target_type varchar(30) NOT NULL CHECK (target_type IN ('user','partner','membership')),
    target_id uuid NOT NULL,
    reason varchar(500),
    old_values jsonb,
    new_values jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX idx_admin_events_target ON admin_events(target_type,target_id,created_at,id);
CREATE INDEX idx_admin_events_actor ON admin_events(actor_id,created_at,id);
CREATE TRIGGER immutable_admin_events BEFORE UPDATE OR DELETE ON admin_events
    FOR EACH ROW EXECUTE FUNCTION preserve_partner_history();
