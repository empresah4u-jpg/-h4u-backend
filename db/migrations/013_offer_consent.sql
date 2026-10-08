-- Additive; historical winners/reservations are not backfilled or reinterpreted.
ALTER TABLE service_requests ADD COLUMN terms_revision bigint NOT NULL DEFAULT 1 CHECK (terms_revision>0);
ALTER TABLE request_partners ADD COLUMN offer_version bigint NOT NULL DEFAULT 0 CHECK (offer_version>=0);
ALTER TABLE request_partners ADD COLUMN offer_request_revision bigint;
ALTER TABLE request_partners ADD COLUMN offer_request_snapshot jsonb;

CREATE FUNCTION request_consent_terms(r service_requests) RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
 SELECT jsonb_build_object('traveler_id',r.traveler_id,'product_id',r.product_id,
 'destination_id',r.destination_id,'session_id',r.session_id,'request_type',r.request_type,
 'service_date',r.service_date,'preferred_time',r.preferred_time,'flexible_time',r.flexible_time,
 'passenger_count',r.passenger_count,'adults_count',r.adults_count,'minors_count',r.minors_count,
 'additional_notes',r.additional_notes);
$$;

CREATE TABLE request_offer_consents (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 service_request_id uuid NOT NULL REFERENCES service_requests(id) ON DELETE RESTRICT,
 request_partner_id uuid NOT NULL UNIQUE REFERENCES request_partners(id) ON DELETE RESTRICT,
 user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 traveler_id uuid NOT NULL REFERENCES travelers(id) ON DELETE RESTRICT,
 partner_id uuid NOT NULL REFERENCES partners(id) ON DELETE RESTRICT,
 product_id uuid NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
 offer_version bigint NOT NULL CHECK(offer_version>0),
 request_revision bigint NOT NULL CHECK(request_revision>0),
 amount_total numeric(12,2) NOT NULL CHECK(amount_total>=0 AND amount_total<>'NaN'::numeric),
 currency varchar(3) NOT NULL CHECK(currency ~ '^[A-Z]{3}$'),
 service_date date NOT NULL,
 service_time time,
 flexible_time boolean NOT NULL,
 passenger_count integer NOT NULL CHECK(passenger_count>0),
 adults_count integer NOT NULL CHECK(adults_count>=0),
 minors_count integer NOT NULL CHECK(minors_count>=0),
 conditions text,
 request_snapshot jsonb NOT NULL CHECK(jsonb_typeof(request_snapshot)='object'),
 accepted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(passenger_count=adults_count+minors_count),
 UNIQUE(service_request_id),
 UNIQUE(id,service_request_id,request_partner_id)
);
ALTER TABLE reservations ADD COLUMN consent_id uuid;
ALTER TABLE reservations ADD CONSTRAINT reservations_consent_fk
 FOREIGN KEY(consent_id,service_request_id,request_partner_id)
 REFERENCES request_offer_consents(id,service_request_id,request_partner_id) ON DELETE RESTRICT;
CREATE UNIQUE INDEX reservations_consent_unique ON reservations(consent_id) WHERE consent_id IS NOT NULL;

CREATE FUNCTION consent_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Consent evidence is immutable' USING ERRCODE='23514'; END $$;
CREATE TRIGGER consent_immutable BEFORE UPDATE OR DELETE ON request_offer_consents FOR EACH ROW EXECUTE FUNCTION consent_immutable();
CREATE TRIGGER consent_no_truncate BEFORE TRUNCATE ON request_offer_consents FOR EACH STATEMENT EXECUTE FUNCTION consent_immutable();

CREATE FUNCTION request_terms_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.assigned_partner_id IS NOT NULL AND NEW.assigned_partner_id IS DISTINCT FROM OLD.assigned_partner_id
    AND NOT EXISTS(SELECT 1 FROM request_offer_consents WHERE service_request_id=NEW.id AND partner_id=NEW.assigned_partner_id) THEN
  RAISE EXCEPTION 'New assignment requires consent' USING ERRCODE='23514';
 END IF;
 IF request_consent_terms(NEW) IS DISTINCT FROM request_consent_terms(OLD) THEN
  IF EXISTS(SELECT 1 FROM request_offer_consents WHERE service_request_id=OLD.id) THEN
   RAISE EXCEPTION 'Consented request terms are immutable' USING ERRCODE='23514';
  END IF;
  NEW.terms_revision=OLD.terms_revision+1;
 ELSE NEW.terms_revision=OLD.terms_revision; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER request_terms_version BEFORE UPDATE ON service_requests FOR EACH ROW EXECUTE FUNCTION request_terms_version();

CREATE FUNCTION offer_terms_version() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r service_requests;
BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.is_winner THEN RAISE EXCEPTION 'New winner requires consent' USING ERRCODE='23514'; END IF;
  NEW.offer_version=0; NEW.offer_request_revision=NULL; NEW.offer_request_snapshot=NULL;
  RETURN NEW;
 END IF;
 IF ROW(NEW.proposed_price,NEW.proposed_currency,NEW.proposed_time,NEW.partner_message,
        NEW.service_request_id,NEW.partner_id,NEW.product_partner_id) IS DISTINCT FROM
    ROW(OLD.proposed_price,OLD.proposed_currency,OLD.proposed_time,OLD.partner_message,
        OLD.service_request_id,OLD.partner_id,OLD.product_partner_id)
    OR NEW.offer_version IS DISTINCT FROM OLD.offer_version THEN
  IF EXISTS(SELECT 1 FROM request_offer_consents WHERE request_partner_id=OLD.id) THEN
   RAISE EXCEPTION 'Consented offer terms are immutable' USING ERRCODE='23514';
  END IF;
  SELECT * INTO STRICT r FROM service_requests WHERE id=NEW.service_request_id;
  NEW.offer_version=OLD.offer_version+1;
  NEW.offer_request_revision=r.terms_revision;
  NEW.offer_request_snapshot=request_consent_terms(r);
 ELSE
  NEW.offer_request_revision=OLD.offer_request_revision;
  NEW.offer_request_snapshot=OLD.offer_request_snapshot;
 END IF;
 IF NEW.is_winner AND NOT OLD.is_winner AND NOT EXISTS(
    SELECT 1 FROM request_offer_consents c WHERE c.request_partner_id=NEW.id
      AND c.service_request_id=NEW.service_request_id AND c.partner_id=NEW.partner_id
      AND c.offer_version=NEW.offer_version) THEN
  RAISE EXCEPTION 'New winner requires consent' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER offer_terms_version BEFORE INSERT OR UPDATE ON request_partners FOR EACH ROW EXECUTE FUNCTION offer_terms_version();

CREATE FUNCTION validate_offer_consent() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r service_requests; o request_partners;
BEGIN
 SELECT * INTO STRICT r FROM service_requests WHERE id=NEW.service_request_id FOR UPDATE;
 SELECT * INTO STRICT o FROM request_partners WHERE id=NEW.request_partner_id FOR UPDATE;
 IF r.status NOT IN ('searching','offers_received') OR r.assigned_partner_id IS NOT NULL
    OR o.status<>'counter_offered' OR o.service_request_id<>r.id
    OR r.expires_at<=clock_timestamp() OR o.expires_at<=clock_timestamp()
    OR o.offer_version<>NEW.offer_version OR o.offer_version=0
    OR o.offer_request_revision IS DISTINCT FROM r.terms_revision
    OR o.offer_request_snapshot IS DISTINCT FROM request_consent_terms(r)
    OR NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.user_id AND role='tourist' AND status='active' AND traveler_id=r.traveler_id)
    OR ROW(NEW.traveler_id,NEW.partner_id,NEW.product_id,NEW.request_revision,NEW.amount_total,NEW.currency,
           NEW.service_date,NEW.service_time,NEW.flexible_time,NEW.passenger_count,NEW.adults_count,NEW.minors_count,NEW.conditions,NEW.request_snapshot)
       IS DISTINCT FROM ROW(r.traveler_id,o.partner_id,r.product_id,r.terms_revision,o.proposed_price,o.proposed_currency::varchar,
           r.service_date,COALESCE(o.proposed_time,r.preferred_time),CASE WHEN o.proposed_time IS NULL THEN r.flexible_time ELSE false END,
           r.passenger_count,r.adults_count,r.minors_count,o.partner_message,request_consent_terms(r)) THEN
  RAISE EXCEPTION 'Invalid consent snapshot' USING ERRCODE='23514';
 END IF;
 NEW.accepted_at=clock_timestamp();
 RETURN NEW;
END $$;
CREATE TRIGGER validate_offer_consent BEFORE INSERT ON request_offer_consents FOR EACH ROW EXECUTE FUNCTION validate_offer_consent();

CREATE FUNCTION reservation_consent_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE c request_offer_consents;
BEGIN
 IF TG_OP='UPDATE' THEN
  IF NEW.consent_id IS DISTINCT FROM OLD.consent_id THEN
   RAISE EXCEPTION 'Consent reference is immutable' USING ERRCODE='23514'; END IF;
  IF OLD.consent_id IS NULL THEN RETURN NEW; END IF;
 END IF;
 IF NEW.consent_id IS NULL THEN RAISE EXCEPTION 'New reservation requires consent' USING ERRCODE='23514'; END IF;
 SELECT * INTO STRICT c FROM request_offer_consents WHERE id=NEW.consent_id;
 IF ROW(NEW.service_request_id,NEW.request_partner_id,NEW.traveler_id,NEW.partner_id,NEW.product_id,
    NEW.agreed_price,NEW.currency::varchar,NEW.service_date,NEW.service_time,NEW.passenger_count)
    IS DISTINCT FROM ROW(c.service_request_id,c.request_partner_id,c.traveler_id,c.partner_id,c.product_id,
    c.amount_total,c.currency,c.service_date,c.service_time,c.passenger_count) THEN
  RAISE EXCEPTION 'Reservation differs from consent' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER reservation_consent_guard BEFORE INSERT OR UPDATE ON reservations FOR EACH ROW EXECUTE FUNCTION reservation_consent_guard();
