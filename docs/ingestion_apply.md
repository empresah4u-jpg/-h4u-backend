# Hotel ingestion: validated plans and transactional apply (011)

011 is additive and deployed through the official runner to persistent DEMO first,
then local H4U. The migration preserves all six protected catalog tables. No real
batches or embedding jobs were submitted. Existing migrations 001–010 remain unchanged.

## Operator workflow (future authorized use)

1. `python -m scripts.create_ingestion_plan input.json > plan.json`
   runs validation/preview in a READ ONLY snapshot. RAW is accepted only here.
2. Review the plan. Its SHA-256 is integrity/idempotency, **not human authorization**.
3. `python -m pipelines.apply_hotels plan.json --dry-run` (also the default).
4. Only after approval: `python -m pipelines.apply_hotels plan.json --apply`.
5. After commit, `python -m scripts.process_ingestion_embeddings --run` processes
   one pending/retryable job. Without --run it only reports queue counts.

## Official deployment

`python -m scripts.apply_ingestion_migration --demo --apply` uses the official
demo setup route and installer. After demo validation,
`python -m scripts.apply_ingestion_migration --apply` deploys to local H4U.
Without --apply the runner rehearses and rolls back. Both paths verify checksums,
objects and protected table fingerprints within the migration transaction.

011 SHA-256: `1b014754107a058a5b87744684d411acd214d3104801f030bd03f3cf7f40ee79`.

The runtime retains SELECT/INSERT only on evidence. A parameterless SECURITY DEFINER
function acquires only the fixed, schema-qualified evidence table lock. Its owner
is the installer, search_path is pg_catalog, pg_temp, and PUBLIC has no EXECUTE.
The installer grants EXECUTE to the demo runtime. No general evidence write/DDL
privileges are added. The new ledger/queue permit SELECT/INSERT and UPDATE only
on their operational state columns; their guards enforce immutable identities.

## Contract and validation

Version 1 contains `contract_version`, `entity_type=hotel`, `plan_id`,
`plan_fingerprint`, original validated `records`, `expected_state` and `entries`.
Each entry holds stable target UUID, catalog/provenance actions, expected hotel,
new values, destination/source IDs, evidence, semantic hash and embedding requirement.
CREATE target IDs derive from destination/code. Default plan IDs derive from the
canonical payload. Canonical JSON sorts keys, retains list order, forbids NaN and
uses UTF-8. Reordering a batch changes its fingerprint; reordering object keys does not.
The digest excludes plan_id and plan_fingerprint.

APPLY reconstructs preview and entries from the expected snapshot, verifies the
whole contract/fingerprint, then compares the snapshot with the locked database.
It never trusts submitted action labels or a recomputed fingerprint as validation.
CONFLICT/REVIEW/REJECT/AMBIGUOUS reject the **entire batch**. No partial apply.
The pending Paracas codes HOT053/HOT071/HOT065 cannot enter an automatic plan.

This first engine accepts active outcomes only. Deactivation is explicitly rejected:
the existing semantic search still sees embeddings for inactive entities, and the
incremental sync reports orphans without deleting them. A separate retirement policy
must be approved before automatic deactivation; no silent stale search result is created.

## Transactions, stale checks and durable replay

A coarse advisory lock plus SHARE ROW EXCLUSIVE locks on hotels, destinations,
sources, links and evidence prevent concurrent writers/phantom matches during apply.
All snapshot rows currently used by preview are compared, including protection fields
and evidence. Unrelated changes can conservatively invalidate a plan; regenerate and
review it, never force it. Dry-run uses REPEATABLE READ READ ONLY and reserves nothing.

One transaction stores the immutable plan, changes hotels, creates/reuses links,
adds evidence, queues semantic changes and records the result. APPLYING is only an
internal transactional state. A deferred constraint trigger forbids committing it.
APPLIED/result become visible together **only on commit**. Errors roll back the ledger
as well as the complete batch; there is no separately committed FAILED plan.

Same ID + different fingerprint is rejected. Same ID + same fingerprint returns the
committed result, even if subsequent operations changed the catalog. Same payload
with another ID returns the original receipt via the unique fingerprint. No duplicate
writes are attempted. A newly generated plan from changed state is a different plan.

Existing evidence/legacy notes are never overwritten. ADD reuses the source/entity
relationship, checks equivalent evidence before INSERT, and relies on 010's server
fingerprint UNIQUE as the final concurrent safeguard. UNCHANGED writes no evidence.

## Embeddings and crash recovery

Only CREATE/reactivation/UPDATE changing canonical semantic text produces a job.
The canonical renderer is shared with generate_embeddings; numeric values retain
PostgreSQL text precision, including trailing decimal zeros. Phone/website-only
updates and provenance-only changes do not queue jobs. An older embedding defect
unrelated to this batch remains the responsibility of the existing reconciliation.
Model and dimensions remain multilingual MiniLM / 384.

The queue has FK to its applied plan and hotel, a unique plan/entity/hash key,
pending/processing/completed/failed states, attempts, sanitized errors and timestamps.
Equivalent work within the same plan cannot duplicate. Jobs from later plans may
share a hash; after the first synchronization, later jobs finish without encoding.

A worker owns one transaction and row lock (`FOR UPDATE SKIP LOCKED`) throughout
processing. PROCESSING is never committed alone. A crash/disconnect releases locks
and rolls the claim/embedding writes back, so the previous pending/failed state is
recoverable. A leftover committed PROCESSING row can also be reclaimed when unlocked.
This is the transaction-lock alternative to a lease; no clock/lease expiration races.
A live long-running transaction holds locks: connection termination/DB operational
monitoring releases them; there is no external lease service.

Embedding writes and job COMPLETED commit atomically. Errors roll back embedding
changes to a savepoint and commit FAILED + attempt count + a fixed safe error code.
No exception text, SQL or credentials are stored. Retry uses the same job. A changed
semantic hash/inactive or missing hotel fails visibly, never overwrites newer content.
If a newer plan superseded it, its own job handles the newer state; failed old jobs
remain for review rather than being silently declared completed.

Worker reuses incremental storage/vector checks and touches only its hotel. If the
vector/model/content/destination already match, no model is loaded. Model loading is
forced offline; missing local cache becomes a visible retryable failure. Catalog apply
does not invoke the worker or imply embeddings are already synchronized.

## Isolation and validation

`python -m scripts.test_ingestion_isolated tests/test_ingestion_apply.py -q`
creates a random database/role on **h4u-demo-postgres only**, imports schema only,
seeds fictional catalog data and runs tests. Cleanup removes only that newly-created
random database and installer/runtime roles. Test schemas exercise 010/011, commits, rollback, concurrent
replay and worker crashes without applying migrations to either real public schema.
Existing tests which explicitly exercise persistent demo continue to do so; all
ordinary test connections target the disposable database, never H4U.

Run the complete suite by omitting the test path. Offline environment flags prohibit
model downloads. This changes neither production credentials nor production data.

Restricted-runtime validation: 52 focused tests passed, including direct lock/DDL
denial, the single privileged lock, PUBLIC denial, full rollback, STALE and durable
idempotence. Persistent DEMO also completed a fictional APPLY and failed/retried
embedding job with a FAKE encoder using its real restricted runtime; audit EXIT=0.
No production embedding was processed.

The earlier full run found two concurrency fixtures using the host day while
PostgreSQL was on the next UTC day. The fixture settlement helper now uses its
commission's PostgreSQL date; production financial code is unchanged.

Final consolidated suite: **555 passed, 1 warning** (known urllib3/LibreSSL),
in 164.63 seconds on the isolated disposable database. Python compilation and
checkpoint whitespace checks passed. H4U retains zero plans and zero jobs.
