-- Commercial lifecycle, reviewed with transactional tests. No economic defaults or backfill.
CREATE TABLE cancellation_policy_versions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 name varchar(120) NOT NULL,
 rules jsonb NOT NULL CHECK(jsonb_typeof(rules)='object'),
 actor_subject varchar(200) NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER immutable_cancellation_policy BEFORE UPDATE OR DELETE ON cancellation_policy_versions
 FOR EACH ROW EXECUTE FUNCTION preserve_financial_ledger();
CREATE TABLE commercial_product_settings (
 product_id uuid PRIMARY KEY REFERENCES products(id),
 request_ttl_seconds integer CHECK(request_ttl_seconds>0),
 offer_ttl_seconds integer CHECK(offer_ttl_seconds>0),
 reservation_ttl_seconds integer CHECK(reservation_ttl_seconds>0),
 payment_ttl_seconds integer CHECK(payment_ttl_seconds>0),
 scheduled_only boolean NOT NULL DEFAULT false,
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE cancellation_policy_assignments (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 product_id uuid NOT NULL REFERENCES products(id),
 partner_id uuid REFERENCES partners(id),
 policy_id uuid NOT NULL REFERENCES cancellation_policy_versions(id),
 active boolean NOT NULL DEFAULT true,
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE UNIQUE INDEX uq_policy_product ON cancellation_policy_assignments(product_id) WHERE partner_id IS NULL;
CREATE UNIQUE INDEX uq_policy_partner ON cancellation_policy_assignments(product_id,partner_id) WHERE partner_id IS NOT NULL;
CREATE TABLE commercial_slots (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 product_id uuid NOT NULL REFERENCES products(id),
 partner_id uuid NOT NULL REFERENCES partners(id),
 service_date date NOT NULL,
 service_time time NOT NULL,
 capacity integer NOT NULL CHECK(capacity>=0),
 reserved integer NOT NULL DEFAULT 0 CHECK(reserved>=0 AND reserved<=capacity),
 active boolean NOT NULL DEFAULT true,
 UNIQUE(product_id,partner_id,service_date,service_time)
);
ALTER TABLE reservations ADD COLUMN slot_id uuid REFERENCES commercial_slots(id);
ALTER TABLE reservations ADD COLUMN expires_at timestamptz;
ALTER TABLE reservations ADD COLUMN service_at timestamptz;
ALTER TABLE reservations ADD COLUMN no_show_at timestamptz;
ALTER TABLE reservations DROP CONSTRAINT reservations_status_check;
ALTER TABLE reservations ADD CONSTRAINT reservations_status_check CHECK(status IN
 ('pending','awaiting_passenger_data','payment_pending','confirmed','ready','completed','cancelled','no_show','expired'));
ALTER TABLE request_partners ADD COLUMN expires_at timestamptz;
ALTER TABLE payments ADD COLUMN expires_at timestamptz;
ALTER TABLE payments DROP CONSTRAINT payments_status_check;
ALTER TABLE payments ADD CONSTRAINT payments_status_check CHECK(status IN
 ('pending','reported','waiting_verification','paid','disputed','failed','cancelled','refunded','partially_refunded','expired'));
CREATE TABLE cancellation_cases (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 reservation_id uuid NOT NULL REFERENCES reservations(id),
 idempotency_key varchar(200) NOT NULL,
 fingerprint char(64) NOT NULL,
 reason varchar(1000) NOT NULL,
 actor_subject varchar(200) NOT NULL,
 actor_role varchar(20) NOT NULL,
 policy_id uuid REFERENCES cancellation_policy_versions(id),
 policy_snapshot jsonb,
 status varchar(40) NOT NULL CHECK(status IN ('requires_manual_review','denied','cancelled')),
 decision jsonb NOT NULL,
 result jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(reservation_id,idempotency_key)
);
CREATE UNIQUE INDEX uq_effective_cancellation ON cancellation_cases(reservation_id) WHERE status='cancelled';
CREATE TABLE refund_commands (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 cancellation_id uuid NOT NULL UNIQUE REFERENCES cancellation_cases(id),
 payment_id uuid NOT NULL REFERENCES payments(id),
 amount numeric(12,2) NOT NULL CHECK(amount>0 AND amount<>'NaN'::numeric),
 currency char(3) NOT NULL,
 status varchar(30) NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','processed')),
 execution_kind varchar(30) CHECK(execution_kind IN ('manual_confirmation','demo_fake')),
 external_reference varchar(200),
 refund_id uuid UNIQUE REFERENCES refunds(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 processed_at timestamptz,
 CHECK((status='processed')=(refund_id IS NOT NULL)),
 CHECK(status<>'processed' OR (execution_kind IS NOT NULL AND external_reference IS NOT NULL))
);
CREATE INDEX idx_reservation_expiry ON reservations(expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX idx_candidate_expiry ON request_partners(expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX idx_payment_expiry ON payments(expires_at) WHERE expires_at IS NOT NULL;

CREATE FUNCTION maintain_commercial_capacity() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE delta integer; s commercial_slots%ROWTYPE;
BEGIN
 IF TG_OP='UPDATE' AND (NEW.slot_id IS DISTINCT FROM OLD.slot_id OR
     NEW.passenger_count<>OLD.passenger_count OR NEW.product_id<>OLD.product_id OR
     NEW.partner_id<>OLD.partner_id OR NEW.service_date<>OLD.service_date OR
     NEW.service_time IS DISTINCT FROM OLD.service_time) THEN
  RAISE EXCEPTION 'Reservation allocation is immutable' USING ERRCODE='23514';
 END IF;
 IF TG_OP='UPDATE' AND OLD.status IN ('cancelled','expired','completed','no_show') AND NEW.status<>OLD.status THEN
  RAISE EXCEPTION 'Terminal reservation cannot reopen' USING ERRCODE='23514';
 END IF;
 IF NEW.slot_id IS NULL THEN RETURN NEW; END IF;
 SELECT * INTO STRICT s FROM commercial_slots WHERE id=NEW.slot_id FOR UPDATE;
 IF (s.product_id,s.partner_id,s.service_date,s.service_time) IS DISTINCT FROM
    (NEW.product_id,NEW.partner_id,NEW.service_date,NEW.service_time) THEN
  RAISE EXCEPTION 'Slot does not match reservation' USING ERRCODE='23514';
 END IF;
 delta:=0;
 IF TG_OP='INSERT' AND NEW.status NOT IN ('cancelled','expired') THEN
  IF NOT s.active THEN RAISE EXCEPTION 'Slot is inactive' USING ERRCODE='23514'; END IF;
  delta:=NEW.passenger_count;
 ELSIF TG_OP='UPDATE' AND OLD.status NOT IN ('cancelled','expired') AND NEW.status IN ('cancelled','expired') THEN
  delta:=-NEW.passenger_count;
 END IF;
 UPDATE commercial_slots SET reserved=reserved+delta WHERE id=s.id AND reserved+delta BETWEEN 0 AND capacity;
 IF NOT FOUND THEN RAISE EXCEPTION 'Insufficient capacity' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER commercial_capacity BEFORE INSERT OR UPDATE ON reservations FOR EACH ROW EXECUTE FUNCTION maintain_commercial_capacity();

CREATE FUNCTION set_commercial_deadline() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE settings commercial_product_settings%ROWTYPE; pid uuid; ttl integer; zone text;
BEGIN
 IF TG_TABLE_NAME='service_requests' THEN pid:=NEW.product_id;
 ELSIF TG_TABLE_NAME='reservations' THEN pid:=NEW.product_id;
 ELSIF TG_TABLE_NAME='request_partners' THEN SELECT product_id INTO pid FROM service_requests WHERE id=NEW.service_request_id;
 ELSE SELECT product_id INTO pid FROM reservations WHERE id=NEW.reservation_id; END IF;
 SELECT * INTO settings FROM commercial_product_settings WHERE product_id=pid;
 IF TG_TABLE_NAME='service_requests' THEN ttl:=settings.request_ttl_seconds;
 ELSIF TG_TABLE_NAME='request_partners' THEN ttl:=settings.offer_ttl_seconds;
 ELSIF TG_TABLE_NAME='reservations' THEN
  ttl:=settings.reservation_ttl_seconds;
  SELECT d.timezone INTO zone FROM products p JOIN destinations d ON d.id=p.destination_id WHERE p.id=pid;
  IF NEW.service_time IS NOT NULL AND zone IS NOT NULL THEN
   NEW.service_at:=(NEW.service_date+NEW.service_time) AT TIME ZONE zone;
  END IF;
 ELSE ttl:=settings.payment_ttl_seconds; END IF;
 IF ttl IS NOT NULL AND NEW.expires_at IS NULL THEN NEW.expires_at:=clock_timestamp()+make_interval(secs=>ttl); END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER commercial_request_deadline BEFORE INSERT ON service_requests FOR EACH ROW EXECUTE FUNCTION set_commercial_deadline();
CREATE TRIGGER commercial_offer_deadline BEFORE INSERT ON request_partners FOR EACH ROW EXECUTE FUNCTION set_commercial_deadline();
CREATE TRIGGER commercial_reservation_deadline BEFORE INSERT ON reservations FOR EACH ROW EXECUTE FUNCTION set_commercial_deadline();
CREATE TRIGGER commercial_payment_deadline BEFORE INSERT ON payments FOR EACH ROW EXECUTE FUNCTION set_commercial_deadline();

ALTER TABLE notification_events DROP CONSTRAINT notification_events_kind_check;
ALTER TABLE notification_events ADD CONSTRAINT notification_events_kind_check CHECK(kind IN
 ('request.created','partner.request','partner.response','reservation.confirmed','payment.confirmed','reservation.cancelled',
 'request.cancelled','request.expired','reservation.expired','refund.processed','reservation.no_show','reservation.completed'));
CREATE FUNCTION capture_lifecycle_notification() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE k text; sr uuid; r uuid; t uuid; p uuid;
BEGIN
 IF current_setting('h4u.messaging_simulation',true)='true' THEN RETURN NEW; END IF;
 IF TG_TABLE_NAME='service_requests' THEN
  IF NEW.status IS NOT DISTINCT FROM OLD.status OR NEW.status NOT IN ('cancelled','expired') THEN RETURN NEW; END IF;
  k:='request.'||NEW.status; sr:=NEW.id; t:=NEW.traveler_id; p:=NEW.assigned_partner_id;
 ELSIF TG_TABLE_NAME='reservations' THEN
  IF NEW.status IS NOT DISTINCT FROM OLD.status OR NEW.status NOT IN ('expired','no_show','completed') THEN RETURN NEW; END IF;
  k:='reservation.'||NEW.status; sr:=NEW.service_request_id; r:=NEW.id; t:=NEW.traveler_id; p:=NEW.partner_id;
 ELSE
  IF NEW.status<>'processed' THEN RETURN NEW; END IF;
  k:='refund.processed';
  SELECT rv.id,rv.service_request_id,rv.traveler_id,rv.partner_id INTO r,sr,t,p FROM payments py JOIN reservations rv ON rv.id=py.reservation_id WHERE py.id=NEW.payment_id;
 END IF;
 IF EXISTS(SELECT 1 FROM service_requests WHERE id=sr AND messaging_suppressed) THEN RETURN NEW; END IF;
 INSERT INTO notification_events(event_key,kind,traveler_id,partner_id,service_request_id,reservation_id)
 VALUES(k||':'||NEW.id::text,k,t,p,sr,r) ON CONFLICT(event_key) DO NOTHING;
 RETURN NEW;
END $$;
CREATE TRIGGER lifecycle_request_event AFTER UPDATE OF status ON service_requests FOR EACH ROW EXECUTE FUNCTION capture_lifecycle_notification();
CREATE TRIGGER lifecycle_reservation_event AFTER UPDATE OF status ON reservations FOR EACH ROW EXECUTE FUNCTION capture_lifecycle_notification();
CREATE TRIGGER lifecycle_refund_event AFTER INSERT OR UPDATE OF status ON refunds FOR EACH ROW EXECUTE FUNCTION capture_lifecycle_notification();
CREATE TRIGGER immutable_cancellation_case BEFORE UPDATE OR DELETE ON cancellation_cases
 FOR EACH ROW EXECUTE FUNCTION preserve_financial_ledger();

CREATE FUNCTION protect_refund_command() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Refund commands cannot be deleted' USING ERRCODE='55000'; END IF;
 IF OLD.status='processed' OR NEW.cancellation_id<>OLD.cancellation_id OR NEW.payment_id<>OLD.payment_id
    OR NEW.amount<>OLD.amount OR NEW.currency<>OLD.currency OR NEW.id<>OLD.id THEN
  RAISE EXCEPTION 'Refund command history is immutable' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER immutable_refund_command BEFORE UPDATE OR DELETE ON refund_commands
 FOR EACH ROW EXECUTE FUNCTION protect_refund_command();
