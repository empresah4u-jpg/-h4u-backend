-- H4U identity layer.
-- Additive migration: identities are independent from business entities.
-- Passwords are never stored in travelers or partners.

CREATE TABLE users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    email varchar(320) NOT NULL,
    password_hash varchar(255) NOT NULL,

    role varchar(20) NOT NULL
        CHECK (role IN ('tourist', 'partner', 'operator', 'admin')),

    traveler_id uuid REFERENCES travelers(id),
    partner_id uuid REFERENCES partners(id),

    status varchar(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'disabled', 'locked')),

    token_version integer NOT NULL DEFAULT 0
        CHECK (token_version >= 0),

    last_login_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),

    CHECK (
        (role = 'tourist' AND traveler_id IS NOT NULL AND partner_id IS NULL)
        OR
        (role = 'partner' AND partner_id IS NOT NULL AND traveler_id IS NULL)
        OR
        (role IN ('operator', 'admin')
            AND traveler_id IS NULL
            AND partner_id IS NULL)
    )
);

CREATE UNIQUE INDEX uq_users_email_lower
    ON users (lower(email));

CREATE UNIQUE INDEX uq_users_traveler
    ON users (traveler_id)
    WHERE traveler_id IS NOT NULL;

CREATE UNIQUE INDEX uq_users_partner
    ON users (partner_id)
    WHERE partner_id IS NOT NULL;

CREATE TABLE auth_sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    token_id uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),

    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,

    created_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz,

    CHECK (expires_at > created_at)
);

CREATE INDEX idx_auth_sessions_user
    ON auth_sessions(user_id);

CREATE INDEX idx_auth_sessions_active
    ON auth_sessions(user_id, expires_at)
    WHERE revoked_at IS NULL;
