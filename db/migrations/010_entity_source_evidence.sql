-- Additive only. Legacy entity_sources evidence remains intact, without backfill.
CREATE TABLE entity_source_evidence (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_source_id uuid NOT NULL REFERENCES entity_sources(id) ON DELETE RESTRICT,
    source_url text,
    source_reference text,
    notes text NOT NULL CHECK (length(btrim(notes)) > 0),
    observed_at timestamptz,
    verification_status varchar(50) NOT NULL DEFAULT 'unverified'
        CHECK (verification_status IN ('unverified','verified','disputed','rejected')),
    verified_at timestamptz,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (source_url IS NOT NULL OR source_reference IS NOT NULL),
    CHECK (source_url IS NULL OR source_url ~ '^https?://[^[:space:]]+$'),
    CHECK (source_reference IS NULL OR length(btrim(source_reference)) > 0),
    CHECK ((verification_status = 'verified') = (verified_at IS NOT NULL)),
    CONSTRAINT entity_source_evidence_dedup UNIQUE (entity_source_id, fingerprint)
);
-- The unique index already covers lookups/FK checks by entity_source_id.
CREATE INDEX entity_source_evidence_observed_idx ON entity_source_evidence(observed_at);

CREATE FUNCTION entity_source_evidence_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'Evidence is append-only; preserve the original observation';
    END IF;
    -- Server computed: callers cannot bypass deduplication with a fabricated hash.
    -- Verification and ingestion time do not change the identity of an observation.
    NEW.fingerprint := encode(sha256(convert_to(jsonb_build_array(
        NEW.source_url, NEW.source_reference, NEW.notes,
        to_char(NEW.observed_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
        NEW.metadata
    )::text, 'UTF8')), 'hex');
    RETURN NEW;
END;
$$;
CREATE TRIGGER entity_source_evidence_immutable
    BEFORE INSERT OR UPDATE OR DELETE ON entity_source_evidence
    FOR EACH ROW EXECUTE FUNCTION entity_source_evidence_guard();
