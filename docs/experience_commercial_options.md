# F2.1.9 — Experience products, read API

The editorial key is not a tour, price, provider or search term. Migration 012 adds
experience_products(destination_id, experience_key, product_id), with multiple
products per experience and explicit publication status. A product can appear in
multiple editorial experiences. Both foreign keys restrict deletion; the composite
product/destination FK prevents cross-destination associations. The product's
source_entity_type/id retains its existing provenance meaning.

012 is additive and seeds nothing. products gains only UNIQUE(id,destination_id).
Keys are ASCII lowercase segments separated by a single hyphen, starting with a
letter, maximum 80 characters. Timestamps default to now(); a future administrative
writer must explicitly maintain updated_at, as with the existing commercial tables.

## Public contract

GET /experiences/PARACAS/ballestas/commercial-options

Destination codes are exact uppercase stable codes (up to 40 characters). Invalid
path syntax: 422. Unknown destination: 404. Valid but unknown editorial key or no
published association: 200 with options:[]. Only active associations and active
products are published; draft/paused/inactive products remain private. Ordering is
product code, then id. Only product_id,code,name,requestable,reason are returned.

Eligibility uses the shared product_accepts_requests helper and identical constant
ELIGIBLE_REQUEST_PARTNER_SQL used in POST /service-requests. An active product with
reservations_enabled=false gets commercial_option_unavailable; no active relation
to an active reservations-enabled partner gets no_eligible_providers. Otherwise
reason=null and requestable=true. No tariffs, partner identities or internal
configuration are exposed. No authentication is needed for this catalog endpoint.

The query runs in a REPEATABLE READ READ ONLY transaction. It does not lock or
reserve inventory. The answer is a snapshot and can become stale. POST still checks
identity/session ownership, destination, payload and product eligibility and selects
candidates again. No availability, quote or successful creation is promised.
Existing POST can create status=created with no candidates; this endpoint is deliberately
more conservative and marks no_eligible_providers, without changing POST semantics.
The extraction changes no simulation, assignment, consent or financial behavior.

## Migration/deployment

Official runner: python -m scripts.apply_experience_products (rollback rehearsal).
Persistent deployment requires explicit --apply. --demo selects the isolated demo
mechanism and role; the runtime receives SELECT only for the association table.
Uses SHA-256 schema_migrations registry, validates predecessors 002–011, advisory
migration lock and in-lock ledger recheck. Already-applied checksum drift aborts;
a matching registered migration validates schema rather than recreating it.
No historical migration was changed. Demo incremental upgrades recognize 012.

012 was deployed through the official runner to persistent demo first, then H4U.
Demo uses existing fictional destination DEMO-551500021422 (not PARACAS); both
its 200/empty response and unknown-destination 404 were verified with the real
runtime. The official demo auditor passed. H4U PARACAS returns 200/options:[].
Pre/post fingerprints of destinations, products, product_partners, partners, tours
and service_requests were identical; experience_products is empty. Ledger checksum:
3274f93b02936d089c803cf43dae003801491a98d8616a5565b089361f5540e6.
The 30 directed tests passed; no full-suite repetition for deployment.
Use a DDL-authorized migration role for future deployments and ensure runtime SELECT;
never broaden runtime DDL privileges. No PROD-BALLESTAS or real associations created;
PROD-TOUR025 remains unchanged. Frontend remains disconnected.
