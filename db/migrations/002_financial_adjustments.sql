-- Additive migration. Apply once in a transaction; never rebuild existing tables.
ALTER TABLE refunds ADD COLUMN idempotency_key varchar(200);
ALTER TABLE refunds ADD COLUMN request_fingerprint char(64);
ALTER TABLE refunds ADD COLUMN result jsonb;
ALTER TABLE refunds ADD COLUMN actor_subject varchar(200);
ALTER TABLE refunds ADD COLUMN actor_role varchar(20);
CREATE UNIQUE INDEX uq_refunds_idempotency ON refunds(payment_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
ALTER TABLE partner_settlements ADD COLUMN reported_amount numeric(12,2)
    CHECK (reported_amount >= 0 AND reported_amount <> 'NaN'::numeric);

CREATE TABLE commission_adjustments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    commission_id uuid NOT NULL REFERENCES commissions(id),
    refund_id uuid NOT NULL UNIQUE REFERENCES refunds(id),
    amount numeric(12,2) NOT NULL CHECK (amount >= 0 AND amount <> 'NaN'::numeric),
    policy_version varchar(40) NOT NULL DEFAULT 'historical_pro_rata_v1',
    actor_subject varchar(200) NOT NULL,
    actor_role varchar(20) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_adjustments_commission ON commission_adjustments(commission_id);

CREATE TABLE settlement_adjustments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    settlement_id uuid NOT NULL REFERENCES partner_settlements(id),
    adjustment_id uuid NOT NULL REFERENCES commission_adjustments(id),
    amount numeric(12,2) NOT NULL CHECK (amount > 0 AND amount <> 'NaN'::numeric),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(settlement_id, adjustment_id)
);
CREATE INDEX idx_settlement_adjustments_source ON settlement_adjustments(adjustment_id);

CREATE TABLE financial_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type varchar(40) NOT NULL,
    entity_id uuid NOT NULL,
    event_type varchar(60) NOT NULL,
    actor_subject varchar(200) NOT NULL,
    actor_role varchar(20) NOT NULL,
    details jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_financial_events_entity ON financial_events(entity_type,entity_id);

CREATE FUNCTION preserve_financial_ledger() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Financial ledger entries are append-only' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER immutable_commission_adjustments BEFORE UPDATE OR DELETE ON commission_adjustments
    FOR EACH ROW EXECUTE FUNCTION preserve_financial_ledger();
CREATE TRIGGER immutable_settlement_adjustments BEFORE UPDATE OR DELETE ON settlement_adjustments
    FOR EACH ROW EXECUTE FUNCTION preserve_financial_ledger();
CREATE TRIGGER immutable_financial_events BEFORE UPDATE OR DELETE ON financial_events
    FOR EACH ROW EXECUTE FUNCTION preserve_financial_ledger();

-- Enforce provenance and prevent over-allocation even for direct SQL writers.
CREATE FUNCTION validate_commission_adjustment() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE c commissions%ROWTYPE; r refunds%ROWTYPE; reversed numeric;
BEGIN
    SELECT * INTO STRICT c FROM commissions WHERE id=NEW.commission_id FOR UPDATE;
    SELECT * INTO STRICT r FROM refunds WHERE id=NEW.refund_id;
    IF r.payment_id IS DISTINCT FROM c.payment_id OR r.currency <> c.currency OR r.status <> 'processed' THEN
        RAISE EXCEPTION 'Refund and commission do not belong to the same paid movement' USING ERRCODE='23514';
    END IF;
    SELECT COALESCE(sum(amount),0) INTO reversed FROM commission_adjustments WHERE commission_id=c.id;
    IF reversed + NEW.amount > c.commission_amount THEN
        RAISE EXCEPTION 'Reversal exceeds original commission' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER check_commission_adjustment BEFORE INSERT ON commission_adjustments
    FOR EACH ROW EXECUTE FUNCTION validate_commission_adjustment();

CREATE FUNCTION validate_settlement_adjustment() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE a commission_adjustments%ROWTYPE; c commissions%ROWTYPE; s partner_settlements%ROWTYPE; used numeric;
BEGIN
    SELECT * INTO STRICT s FROM partner_settlements WHERE id=NEW.settlement_id FOR UPDATE;
    SELECT * INTO STRICT a FROM commission_adjustments WHERE id=NEW.adjustment_id FOR UPDATE;
    SELECT * INTO STRICT c FROM commissions WHERE id=a.commission_id;
    IF s.partner_id <> c.partner_id OR s.currency <> c.currency OR s.status IN ('paid','cancelled','disputed') THEN
        RAISE EXCEPTION 'Adjustment is incompatible with settlement' USING ERRCODE='23514';
    END IF;
    SELECT COALESCE(sum(amount),0) INTO used FROM settlement_adjustments WHERE adjustment_id=a.id;
    IF used + NEW.amount > a.amount THEN
        RAISE EXCEPTION 'Adjustment credit is already allocated' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER check_settlement_adjustment BEFORE INSERT ON settlement_adjustments
    FOR EACH ROW EXECUTE FUNCTION validate_settlement_adjustment();
