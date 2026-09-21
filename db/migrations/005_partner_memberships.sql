-- Multiuser businesses. No identity/financial rows are deleted or rewritten.
-- The runner verifies 001-004 where recorded and applies this file atomically.
CREATE INDEX idx_users_partner ON users(partner_id) WHERE partner_id IS NOT NULL;
DROP INDEX uq_users_partner; -- index only: remove the one-person-per-business restriction

ALTER TABLE partners ADD COLUMN suspension_source varchar(20)
    CHECK (suspension_source IN ('administrative','debt','legacy'));
UPDATE partners SET suspension_source='legacy' WHERE status='suspended';
ALTER TABLE partners ADD CONSTRAINT partner_suspension_source_state
    CHECK (suspension_source IS NULL OR status='suspended');

CREATE TABLE partner_memberships (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    partner_id uuid NOT NULL REFERENCES partners(id),
    user_id uuid NOT NULL REFERENCES users(id),
    membership_role varchar(20) NOT NULL CHECK (membership_role IN ('owner','manager','staff')),
    status varchar(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','revoked')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(partner_id,user_id)
);
CREATE INDEX idx_memberships_user_active ON partner_memberships(user_id,partner_id) WHERE status='active';

CREATE TABLE partner_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    partner_id uuid NOT NULL REFERENCES partners(id),
    membership_id uuid REFERENCES partner_memberships(id),
    event_type varchar(40) NOT NULL,
    actor_subject varchar(200) NOT NULL,
    old_values jsonb,
    new_values jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_partner_events_partner ON partner_events(partner_id,created_at,id);
CREATE INDEX idx_partner_events_membership ON partner_events(membership_id,created_at,id);

CREATE FUNCTION preserve_partner_history() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Partner history must be retained' USING ERRCODE='55000';
END;
$$;
CREATE TRIGGER immutable_partner_events BEFORE UPDATE OR DELETE ON partner_events
    FOR EACH ROW EXECUTE FUNCTION preserve_partner_history();
CREATE TRIGGER retain_partner_memberships BEFORE DELETE ON partner_memberships
    FOR EACH ROW EXECUTE FUNCTION preserve_partner_history();

CREATE FUNCTION validate_partner_membership() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='UPDATE' AND (NEW.user_id<>OLD.user_id OR NEW.partner_id<>OLD.partner_id OR NEW.id<>OLD.id) THEN
        RAISE EXCEPTION 'Membership identity cannot be reassigned' USING ERRCODE='23514';
    END IF;
    PERFORM id FROM users WHERE id=NEW.user_id AND role='partner' FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Membership requires a partner user' USING ERRCODE='23514';
    END IF;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END;
$$;
CREATE TRIGGER validate_partner_membership BEFORE INSERT OR UPDATE ON partner_memberships
    FOR EACH ROW EXECUTE FUNCTION validate_partner_membership();

CREATE FUNCTION audit_partner_membership() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE previous jsonb; kind text;
BEGIN
    IF TG_OP='UPDATE' THEN
        IF NEW.membership_role=OLD.membership_role AND NEW.status=OLD.status THEN RETURN NEW; END IF;
        previous := jsonb_build_object('user_id',OLD.user_id,'membership_role',OLD.membership_role,'status',OLD.status);
        kind := CASE WHEN NEW.status<>OLD.status THEN 'membership_' || NEW.status ELSE 'membership_role_changed' END;
    ELSE kind := 'membership_created'; END IF;
    INSERT INTO partner_events(partner_id,membership_id,event_type,actor_subject,old_values,new_values)
    VALUES (NEW.partner_id,NEW.id,kind,
        COALESCE(NULLIF(current_setting('h4u.partner_actor',true),''),'database:' || current_user), previous,
        jsonb_build_object('user_id',NEW.user_id,'membership_role',NEW.membership_role,'status',NEW.status));
    RETURN NEW;
END;
$$;
CREATE TRIGGER audit_partner_membership AFTER INSERT OR UPDATE ON partner_memberships
    FOR EACH ROW EXECUTE FUNCTION audit_partner_membership();

CREATE FUNCTION audit_partner_state() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.status,NEW.suspension_source,NEW.reservations_enabled)
       IS DISTINCT FROM ROW(OLD.status,OLD.suspension_source,OLD.reservations_enabled) THEN
        INSERT INTO partner_events(partner_id,event_type,actor_subject,old_values,new_values)
        VALUES (NEW.id,'partner_state_changed',
            COALESCE(NULLIF(current_setting('h4u.partner_actor',true),''),'database:' || current_user),
            jsonb_build_object('status',OLD.status,'suspension_source',OLD.suspension_source,'reservations_enabled',OLD.reservations_enabled),
            jsonb_build_object('status',NEW.status,'suspension_source',NEW.suspension_source,'reservations_enabled',NEW.reservations_enabled));
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER audit_partner_state AFTER UPDATE ON partners
    FOR EACH ROW EXECUTE FUNCTION audit_partner_state();

-- One-time compatibility backfill. No ongoing implicit grant from users.partner_id.
SELECT set_config('h4u.partner_actor','migration:005',true);
INSERT INTO partner_memberships(partner_id,user_id,membership_role,status)
    SELECT partner_id,id,'owner',CASE WHEN status='active' THEN 'active' ELSE 'suspended' END
    FROM users WHERE role='partner';
