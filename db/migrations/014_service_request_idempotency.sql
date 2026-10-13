-- Additive: historical requests remain untouched. Completed records only.
CREATE TABLE service_request_idempotency (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    traveler_id uuid NOT NULL REFERENCES travelers(id) ON DELETE RESTRICT,
    idempotency_key varchar(200) NOT NULL CHECK (length(btrim(idempotency_key)) > 0),
    request_fingerprint varchar(64) NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    service_request_id uuid NOT NULL REFERENCES service_requests(id) ON DELETE RESTRICT,
    response_status integer NOT NULL CHECK (response_status = 201),
    response_body jsonb NOT NULL CHECK (jsonb_typeof(response_body) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(traveler_id, idempotency_key)
);
CREATE INDEX service_request_idempotency_request_idx ON service_request_idempotency(service_request_id);
CREATE FUNCTION immutable_service_request_attempt() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Completed request attempts are immutable'; END $$;
CREATE TRIGGER immutable_service_request_attempt BEFORE UPDATE OR DELETE OR TRUNCATE
ON service_request_idempotency FOR EACH STATEMENT EXECUTE FUNCTION immutable_service_request_attempt();
REVOKE ALL ON service_request_idempotency FROM PUBLIC;
-- Explicit installer-selected runtime; no schema-wide grants or runtime ownership.
DO $$ DECLARE runtime_role text := current_setting('h4u.idempotency_runtime_role'); BEGIN
 IF runtime_role = current_user OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=runtime_role AND NOT rolsuper AND NOT rolcreaterole AND NOT rolcreatedb AND NOT rolbypassrls) THEN
   RAISE EXCEPTION 'Restricted runtime distinct from migrator required';
 END IF;
 EXECUTE format('REVOKE ALL ON service_request_idempotency FROM %I',runtime_role);
 EXECUTE format('GRANT SELECT, INSERT ON service_request_idempotency TO %I',runtime_role);
END $$;
