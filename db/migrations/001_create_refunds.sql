-- H4U
-- Migration 001: Refunds
-- Permite registrar reembolsos parciales y totales
-- asociados a un pago sin eliminar el historial financiero.

CREATE TABLE refunds (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    code VARCHAR(40) NOT NULL UNIQUE,

    payment_id UUID NOT NULL
        REFERENCES payments(id),

    amount NUMERIC(12,2) NOT NULL
        CHECK (amount > 0),

    currency CHAR(3) NOT NULL DEFAULT 'PEN',

    reason TEXT NOT NULL,

    status VARCHAR(30) NOT NULL DEFAULT 'processed'
        CHECK (
            status IN (
                'pending',
                'processed',
                'failed',
                'cancelled'
            )
        ),

    refund_method VARCHAR(30)

    CHECK (

        refund_method IS NULL

        OR refund_method IN (

            'yape',

            'plin',

            'bank_transfer',

            'card',

            'mercado_pago',

            'izipay',

            'cash',

            'other'

        )

    ),

    external_reference VARCHAR(200),

    processed_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE INDEX idx_refunds_payment
    ON refunds(payment_id);


CREATE INDEX idx_refunds_status
    ON refunds(status);


CREATE INDEX idx_refunds_payment_status
    ON refunds(payment_id, status);