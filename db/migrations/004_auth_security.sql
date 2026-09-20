-- Additive login throttling, shared by all API workers. No credential values stored.
CREATE TABLE auth_login_limits (
    key_hash char(64) PRIMARY KEY,
    window_started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    attempts integer NOT NULL CHECK (attempts > 0)
);
CREATE INDEX idx_auth_login_limits_window ON auth_login_limits(window_started_at);

-- A credential or authorization change must not resurrect an older bearer token.
CREATE FUNCTION advance_identity_token_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.token_version < OLD.token_version THEN
        RAISE EXCEPTION 'token_version cannot decrease' USING ERRCODE='23514';
    END IF;
    IF NEW.password_hash IS DISTINCT FROM OLD.password_hash
       OR NEW.role IS DISTINCT FROM OLD.role
       OR NEW.traveler_id IS DISTINCT FROM OLD.traveler_id
       OR NEW.partner_id IS DISTINCT FROM OLD.partner_id
       OR NEW.status IS DISTINCT FROM OLD.status THEN
        NEW.token_version := GREATEST(NEW.token_version, OLD.token_version + 1);
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER advance_identity_token_version
    BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION advance_identity_token_version();

CREATE FUNCTION revoke_changed_identity_sessions() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE auth_sessions SET revoked_at=COALESCE(revoked_at,clock_timestamp())
        WHERE user_id=NEW.id AND revoked_at IS NULL;
    RETURN NEW;
END;
$$;
CREATE TRIGGER revoke_changed_identity_sessions
    AFTER UPDATE ON users FOR EACH ROW
    WHEN (NEW.token_version IS DISTINCT FROM OLD.token_version)
    EXECUTE FUNCTION revoke_changed_identity_sessions();
