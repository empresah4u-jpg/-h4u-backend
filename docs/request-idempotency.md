# 014 — authenticated commercial requests

Deployed under explicit authorization to persistent DEMO first, then H4U local. No cloud deployment and no frontend submission. The default installer still accepts disposable databases only. Controlled deployment requires an explicit target, --apply and the exact approved checksum; it verifies the local Docker context, pinned PostgreSQL system identity, migrator, restricted runtime, prerequisite ledger and historical fingerprints. Without --apply it rehearses and rolls back. Reapplying to an already-migrated persistent target is rejected for manual review.

## Contract
`POST /commercial-sessions` takes only `{destination: "PARACAS"}` and a tourist bearer identity. It derives traveler from the verified user, revalidates/locks the user in the transaction, serializes session resolution on that traveler with NO KEY UPDATE (compatible with foreign-key KEY SHARE), and reuses the oldest active, not-ended `web` session for that destination. Other channels/anonymous sessions remain untouched. There is no new TTL; active sessions remain reusable until explicitly ended/closed. It returns `session_id` and destination, not arbitrary owner identifiers.

`POST /service-requests` retains its payload and now requires `Idempotency-Key` (1–200 characters, no surrounding whitespace) for every HTTP caller. Unknown payload fields are rejected. Reuse the same key and original payload after a timeout. Never generate a fresh key solely to retry. In-process trusted historical fixtures may still call the Python function without a key; there is no unkeyed HTTP route.

JWT and role authorization are unchanged; identity is revalidated/locked again inside the mutation transaction. Ownership is rechecked under session lock. Active destination, matching active/enabled product, valid counts and at least one eligible product-partner relation are required for ordinary requests. Existing staff-only simulation remains distinct and is not a tourist bypass. No new WhatsApp code.

Destination `timezone` is interpreted as IANA. Past dates and supplied past local times are rejected; a nonflexible request needs a time. Invalid zones are 409; bad dates/times are 422. Ambiguous/nonexistent DST wall times are rejected rather than guessed. A requested time is never confirmed availability. Current instant comes from server UTC and is converted to the destination zone.

## Atomicity and retention
SHA-256 over Pydantic-normalized JSON, sorted keys, compact separators, UTF-8, all payload fields including defaults. No tokens or technical timestamps. Session ID is part of intent; retries must keep it. Whitespace in notes is meaningful (not silently rewritten).

A transaction-level PostgreSQL advisory lock keyed by traveler + attempt precedes lookup. READ COMMITTED sees the prior commit after lock acquisition. The UNIQUE constraint remains a DB backstop; hash collisions only serialize unrelated attempts. Completed records are inserted after request, candidates and response are available, in the same transaction. No provisional row, UPDATE, DELETE or TRUNCATE is needed. The response is the original 201 JSON (not subsequently changed request state). Ownership/current identity is still checked on replay, but closed sessions and subsequently disabled products do not prevent an owned completed replay.

Same key/payload returns original 201. Changed payload returns 409. Different travelers have independent keys. Failure before COMMIT rolls back all rows and releases the claim. Lost connection before confirmed commit has an uncertain outcome: retry the SAME key. If commit succeeded, replay returns the original response; if it did not, retry creates it once. Do not promise client delivery exactly once—only one committed creation per retained logical key. No automatic retention/deletion: approximately one record plus response JSON and indexes per accepted attempt; growth is linear. Any future archival policy must preserve deduplication.

## Privileges and validation
Table FKs use RESTRICT, fingerprint is lowercase SHA-256, uniqueness is (traveler_id,idempotency_key), response_status is 201. Statement trigger rejects UPDATE/DELETE/TRUNCATE including owner operations. Installer owns objects; an explicitly selected restricted runtime gets SELECT/INSERT only. Runtime policy also adds INSERT on sessions and UPDATE(id) on destinations solely for row locks, following existing grant conventions. No financial privileges broadened.

Use `python -m scripts.test_ingestion_isolated -q tests/test_request_idempotency.py tests/test_commercial_e2e.py`. The runner copies schema only from isolated demo, creates synthetic identities in a disposable DB, applies 014 (including rollback rehearsal and repeat installation), runs real JWT/restricted connections and independent-process races, then deletes only its generated database/roles and verifies cleanup. No persistent data is copied. Cloud deployment remains unavailable. Controlled local targets are demo and h4u-local only.

Compatibility: old HTTP clients must supply a stable key; direct fixture callers are not a supported public API. Frontend remains disabled. Session timeout policy, archival, and BFF trusted-client IP attribution remain separate deployment decisions. Full suite not run in this checkpoint by instruction.


## F2.2.3.2 deployment verification

Checksum in both ledgers: `fe003c2a4509e940d71cd8bf9dcbf0fe87e5b8f4870e4551f4f51ea23a037aa4`.
All historical row fingerprints/counts matched before/after each migration. Idempotency table: zero records in each DB. No persistent simulation or authenticated production operation was performed.

| Table | DEMO PRE=POST | H4U PRE=POST |
|---|---:|---:|
| service_requests | 212 | 6 |
| reservations | 195 | 5 |
| payments | 178 | 4 |
| request_offer_consents | 6 | 0 |
| sessions | 197 | 1 |

Runtime SELECT/INSERT allowed, UPDATE/DELETE/TRUNCATE and schema CREATE denied on verified policies. Eight constraints and three indexes present for the new table. Ballestas remains draft/disabled/zero partners; TOUR025 remains draft/disabled/one association. Migration 001 was incorporated historically and is not retroactively registered; 002–013 checksums matched before 014.

DEMO uses `demo:<run_id>:<operation>` for a stable logical request key; retries in the same run reuse it and separate runs differ. The disposable test runner excludes 014 objects from its schema clone so rollback/reapplication tests remain meaningful. DEMO setup preserves the immutable table's restricted grants if permissions are later configured.

Directed validation: 29 passed, disposable DB/roles cleanup verified. A nondeterministic expiry fixture was corrected to keep created_at before expires_at; no authentication rule changed. Safe local smoke: health 200, OpenAPI routes registered, anonymous commercial-sessions 401, Ballestas options empty. Old catalog-only container on 8080 was not modified; current frontend targets 8000.
