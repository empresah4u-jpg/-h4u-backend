-- DEMO DATABASE ONLY. Not a production migration. Never run against h4u.
CREATE TABLE demo_environment (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 purpose text NOT NULL CHECK(purpose='h4u-persistent-demo'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
INSERT INTO demo_environment(purpose) VALUES ('h4u-persistent-demo');
CREATE TABLE demo_runs (
 id uuid PRIMARY KEY,
 scenario text NOT NULL CHECK(scenario IN ('accept','counter_offer')),
 status text NOT NULL DEFAULT 'running' CHECK(status IN ('running','completed','failed')),
 summary jsonb NOT NULL DEFAULT '{}',
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 finished_at timestamptz
);
CREATE TABLE demo_message_receipts (
 message_key uuid PRIMARY KEY,
 provider_message_id text NOT NULL UNIQUE,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
