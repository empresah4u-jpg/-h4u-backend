-- Additive infrastructure only. No products or editorial associations are seeded.
ALTER TABLE products ADD CONSTRAINT products_id_destination_unique UNIQUE (id, destination_id);

CREATE TABLE experience_products (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    destination_id uuid NOT NULL REFERENCES destinations(id) ON DELETE RESTRICT,
    experience_key varchar(80) NOT NULL,
    product_id uuid NOT NULL,
    status varchar(20) NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'active', 'inactive')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT experience_products_key_check CHECK (experience_key ~ '^[a-z][a-z0-9]*(-[a-z0-9]+)*$'),
    CONSTRAINT experience_products_product_destination_fk FOREIGN KEY (product_id, destination_id)
        REFERENCES products(id, destination_id) ON DELETE RESTRICT,
    CONSTRAINT experience_products_unique UNIQUE (destination_id, experience_key, product_id)
);
CREATE INDEX experience_products_product_idx ON experience_products(product_id, destination_id);
