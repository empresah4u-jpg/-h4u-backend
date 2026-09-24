ALTER TABLE service_requests ADD COLUMN messaging_suppressed boolean NOT NULL DEFAULT false;
-- Channel transport cannot require a tourist session/destination for unknown senders.
CREATE TABLE channel_threads (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 channel varchar(20) NOT NULL DEFAULT 'whatsapp' CHECK(channel='whatsapp'),
 provider varchar(20) NOT NULL DEFAULT 'meta' CHECK(provider='meta'),
 account_id varchar(40) NOT NULL,
 address varchar(20) NOT NULL CHECK(address ~ '^[0-9]{6,20}$'),
 conversation_id uuid REFERENCES conversations(id),
 traveler_id uuid REFERENCES travelers(id),
 partner_id uuid REFERENCES partners(id),
 bound_by uuid REFERENCES users(id),
 binding_reason varchar(500),
 opted_in_at timestamptz,
 last_inbound_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(provider,account_id,address),
 CHECK(num_nonnulls(traveler_id,partner_id)<=1),
 CHECK((traveler_id IS NULL AND partner_id IS NULL) OR (bound_by IS NOT NULL AND binding_reason IS NOT NULL))
);
CREATE TABLE inbound_messages (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 thread_id uuid NOT NULL REFERENCES channel_threads(id),
 provider_message_id varchar(200) NOT NULL,
 message_type varchar(30) NOT NULL,
 text varchar(4096),
 occurred_at timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(thread_id,provider_message_id)
);
-- Minimal domain facts captured atomically, with no contact data or credentials.
CREATE TABLE notification_events (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 event_key varchar(220) NOT NULL UNIQUE,
 kind varchar(40) NOT NULL CHECK(kind IN ('request.created','partner.request','partner.response','reservation.confirmed','payment.confirmed','reservation.cancelled')),
 traveler_id uuid REFERENCES travelers(id),
 partner_id uuid REFERENCES partners(id),
 service_request_id uuid REFERENCES service_requests(id),
 reservation_id uuid REFERENCES reservations(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 processed_at timestamptz
);
CREATE TABLE message_outbox (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 thread_id uuid NOT NULL REFERENCES channel_threads(id),
 event_id uuid REFERENCES notification_events(id),
 idempotency_key varchar(220) NOT NULL UNIQUE,
 message_type varchar(20) NOT NULL CHECK(message_type IN ('text','template')),
 content jsonb NOT NULL,
 status varchar(20) NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','processing','sent','delivered','read','failed')),
 provider_message_id varchar(200),
 attempt_count integer NOT NULL DEFAULT 0 CHECK(attempt_count BETWEEN 0 AND 5),
 next_attempt_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 processing_at timestamptz,
 last_error varchar(60),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 sent_at timestamptz,
 delivered_at timestamptz,
 read_at timestamptz,
 failed_at timestamptz,
 UNIQUE(thread_id,provider_message_id)
);
CREATE INDEX idx_outbox_pending ON message_outbox(next_attempt_at,id) WHERE status='pending';
CREATE INDEX idx_notifications_pending ON notification_events(created_at,id) WHERE processed_at IS NULL;
CREATE INDEX idx_inbound_thread ON inbound_messages(thread_id,occurred_at);

CREATE FUNCTION capture_channel_notification() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE kind text; k text; t uuid; p uuid; sr uuid; r uuid;
BEGIN
 IF current_setting('h4u.messaging_simulation',true)='true' THEN RETURN NEW; END IF;
 IF TG_TABLE_NAME='service_requests' THEN
  kind:='request.created'; sr:=NEW.id; t:=NEW.traveler_id; k:=NEW.id::text;
 ELSIF TG_TABLE_NAME='request_partners' THEN
  sr:=NEW.service_request_id;
  IF TG_OP='INSERT' THEN
   kind:='partner.request'; p:=NEW.partner_id; k:=NEW.id::text;
  ELSIF NEW.status IS DISTINCT FROM OLD.status AND NEW.status IN ('accepted','rejected','counter_offered') THEN
   kind:='partner.response'; k:=NEW.id::text||':'||NEW.status;
   SELECT traveler_id INTO t FROM service_requests WHERE id=sr;
  ELSE RETURN NEW; END IF;
 ELSIF TG_TABLE_NAME='reservations' THEN
  IF TG_OP='UPDATE' AND NEW.status IS NOT DISTINCT FROM OLD.status THEN RETURN NEW; END IF;
  IF NEW.status='confirmed' THEN kind:='reservation.confirmed';
  ELSIF NEW.status='cancelled' THEN kind:='reservation.cancelled';
  ELSE RETURN NEW; END IF;
  r:=NEW.id; sr:=NEW.service_request_id; t:=NEW.traveler_id; p:=NEW.partner_id; k:=NEW.id::text;
 ELSIF TG_TABLE_NAME='payments' THEN
  IF NEW.status<>'paid' THEN RETURN NEW; END IF;
  IF TG_OP='UPDATE' AND OLD.status='paid' THEN RETURN NEW; END IF;
  kind:='payment.confirmed'; r:=NEW.reservation_id; k:=NEW.id::text;
  SELECT traveler_id,partner_id,service_request_id INTO t,p,sr FROM reservations WHERE id=r;
 END IF;
 IF EXISTS(SELECT 1 FROM service_requests WHERE id=sr AND messaging_suppressed) THEN RETURN NEW; END IF;
 INSERT INTO notification_events(event_key,kind,traveler_id,partner_id,service_request_id,reservation_id)
 VALUES(kind||':'||k,kind,t,p,sr,r) ON CONFLICT(event_key) DO NOTHING;
 RETURN NEW;
END $$;
CREATE TRIGGER messaging_request AFTER INSERT ON service_requests FOR EACH ROW EXECUTE FUNCTION capture_channel_notification();
CREATE TRIGGER messaging_candidate AFTER INSERT OR UPDATE OF status ON request_partners FOR EACH ROW EXECUTE FUNCTION capture_channel_notification();
CREATE TRIGGER messaging_reservation AFTER INSERT OR UPDATE OF status ON reservations FOR EACH ROW EXECUTE FUNCTION capture_channel_notification();
CREATE TRIGGER messaging_payment AFTER INSERT OR UPDATE OF status ON payments FOR EACH ROW EXECUTE FUNCTION capture_channel_notification();
CREATE TABLE channel_binding_events (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 thread_id uuid NOT NULL REFERENCES channel_threads(id),
 actor_id uuid NOT NULL REFERENCES users(id),
 consent boolean NOT NULL,
 reason varchar(500) NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER immutable_channel_binding BEFORE UPDATE OR DELETE ON channel_binding_events
 FOR EACH ROW EXECUTE FUNCTION preserve_partner_history();

CREATE FUNCTION suppress_simulated_messaging() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 NEW.messaging_suppressed := COALESCE(current_setting('h4u.messaging_simulation',true)='true',false);
 RETURN NEW;
END $$;
CREATE TRIGGER messaging_simulation BEFORE INSERT ON service_requests
 FOR EACH ROW EXECUTE FUNCTION suppress_simulated_messaging();
