-- Additive ingestion ledger and transactional embedding outbox. No catalog writes.
CREATE TABLE ingestion_plans (
    id uuid PRIMARY KEY,
    contract_version integer NOT NULL CHECK (contract_version = 1),
    plan_fingerprint text NOT NULL UNIQUE CHECK (plan_fingerprint ~ '^[0-9a-f]{64}$'),
    entity_type text NOT NULL CHECK (entity_type = 'hotel'),
    plan jsonb NOT NULL CHECK (jsonb_typeof(plan) = 'object'),
    status text NOT NULL CHECK (status IN ('applying','applied')),
    result jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    applied_at timestamptz,
    CHECK ((plan->>'plan_id'=id::text AND plan->>'plan_fingerprint'=plan_fingerprint
        AND plan->>'entity_type'=entity_type AND plan->>'contract_version'=contract_version::text) IS TRUE),
    CHECK ((status='applied') = (result IS NOT NULL AND applied_at IS NOT NULL)),
    CHECK (status<>'applying' OR (result IS NULL AND applied_at IS NULL))
);
CREATE FUNCTION ingestion_plan_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Ingestion ledger is immutable'; END IF;
    IF TG_OP='UPDATE' AND (OLD.status='applied' OR
        (NEW.id,NEW.contract_version,NEW.plan_fingerprint,NEW.entity_type,NEW.plan,NEW.created_at)
        IS DISTINCT FROM (OLD.id,OLD.contract_version,OLD.plan_fingerprint,OLD.entity_type,OLD.plan,OLD.created_at)) THEN
        RAISE EXCEPTION 'Ingestion plan identity/result is immutable';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER ingestion_plan_immutable BEFORE UPDATE OR DELETE ON ingestion_plans
FOR EACH ROW EXECUTE FUNCTION ingestion_plan_guard();
-- No committed APPLYING row: failed batches, including their ledger, roll back.
CREATE FUNCTION ingestion_plan_committed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM ingestion_plans WHERE id=NEW.id AND status<>'applied') THEN
        RAISE EXCEPTION 'Cannot commit unfinished ingestion plan';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER ingestion_plan_finished AFTER INSERT OR UPDATE ON ingestion_plans
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ingestion_plan_committed();

CREATE TABLE ingestion_embedding_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ingestion_plan_id uuid NOT NULL REFERENCES ingestion_plans(id) ON DELETE RESTRICT,
    entity_type text NOT NULL CHECK (entity_type='hotel'),
    entity_id uuid NOT NULL REFERENCES hotels(id) ON DELETE RESTRICT,
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','completed','failed')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts>=0),
    last_error text CHECK (last_error IS NULL OR last_error ~ '^[A-Za-z_]{1,80}$'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at timestamptz,
    UNIQUE (ingestion_plan_id,entity_type,entity_id,content_hash),
    CHECK ((status='pending' AND attempts=0) OR (status<>'pending' AND attempts>0)),
    CHECK ((status='completed') = (completed_at IS NOT NULL)),
    CHECK ((status='failed') = (last_error IS NOT NULL))
);
CREATE INDEX ingestion_embedding_jobs_ready ON ingestion_embedding_jobs(created_at,id)
WHERE status IN ('pending','failed','processing');
CREATE FUNCTION ingestion_job_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Ingestion job history is retained'; END IF;
    IF (NEW.id,NEW.ingestion_plan_id,NEW.entity_type,NEW.entity_id,NEW.content_hash,NEW.created_at)
       IS DISTINCT FROM (OLD.id,OLD.ingestion_plan_id,OLD.entity_type,OLD.entity_id,OLD.content_hash,OLD.created_at)
       OR OLD.status='completed'
       OR (NEW.status='processing' AND NEW.attempts<>OLD.attempts+1)
       OR (NEW.status<>'processing' AND NEW.attempts<>OLD.attempts)
       OR (OLD.status IN ('pending','failed') AND NEW.status<>'processing')
       OR (OLD.status='processing' AND NEW.status NOT IN ('processing','completed','failed')) THEN
        RAISE EXCEPTION 'Invalid ingestion job transition';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER ingestion_job_transition BEFORE UPDATE OR DELETE ON ingestion_embedding_jobs
FOR EACH ROW EXECUTE FUNCTION ingestion_job_guard();

-- Narrow capability: no parameters, no dynamic SQL, no row access or mutation.
-- Owned by the controlled installer; runtime gets only EXECUTE during deployment.
CREATE FUNCTION lock_ingestion_source_evidence() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    LOCK TABLE public.entity_source_evidence IN SHARE ROW EXCLUSIVE MODE;
END $$;
REVOKE ALL ON FUNCTION lock_ingestion_source_evidence() FROM PUBLIC;
