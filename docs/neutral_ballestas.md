# F2.1.10 — Neutral Ballestas draft

Provisioning is separate from schema: `python -m scripts.provision_ballestas`
rehearses and rolls back; `--apply` commits. It targets H4U explicitly, resolves
PARACAS by stable code, and creates only PROD-BALLESTAS and its draft association.
No hardcoded database UUIDs, textual operator matching or provider provisioning.
No historical migrations changed.

The product is `Salida Islas Ballestas`, type tour, slug
paracas-salida-islas-ballestas, booking_mode=request,
confirmation_mode=partner_confirmation. Both source_entity fields, price_from and
currency are NULL. Status=draft and reservations_enabled=false.
The association PARACAS/ballestas is draft. The public endpoint returns options:[].

requires_payment=true is a conservative technical placeholder, not a commercial
policy approval: false currently allows the passengers handler to move a reservation
to confirmed without passing payment_pending. Keeping true avoids choosing that
bypass as a default. Activation MUST await an explicit payment-policy decision,
agreed consent rules, service scope and approved eligible providers. No reservation
or payment is created by this script; it cannot activate the product.

The existing pending, disabled PARTNER-OP001 serves a separately catalog-linked
product. That is not evidence of eligibility for this neutral service. Zero provider
associations were created. Required evidence: confirmed service scope/inclusions,
provider capability and commercial eligibility approved for the neutral product.
PROD-TOUR025, its source and providers remain unchanged.

The transaction serializes provisioning, validates the full expected draft tuple,
and stops on conflicting code/slug/association, later activation or provider links.
It never overwrites such changes. Repetition returns the same IDs without UPDATE.
A future legitimate commercial change requires a different approved workflow;
rerunning this seed is not a way to reset data. Integrity fingerprints exclude only
the designated new product and its associations and cover historical catalog and
commercial/financial tables. Integrity mismatch aborts the transaction.

Validation: 72 directed tests passed (5 provisioning, 30 experience options,
37 commercial). An explicit dry-run was rolled back, application succeeded, and a
second application created nothing. Public endpoint HTTP 200/options:[]; PRE/POST
historical fingerprints identical. No full-suite repetition: no runtime handler,
frontend or business-rule changes. No commit/push in this checkpoint.
