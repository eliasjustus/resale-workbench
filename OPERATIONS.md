# Private operations foundation

The optional `operations` Python package supplies strict companion records and a
local journal for offline development. It does not yet expose a `resale ops` CLI
or perform external actions. Local human contexts require OS identity and explicit
reviewed grants; there is no default operator authority. Resource calculations
require explicitly reviewed local evidence. Existing evaluation and frozen run
configuration remain separate.

## Adopted records

`operations/resources/contracts.schema.json` is operational schema version 1,
distinct from economic schema-1 historical replay. It explicitly adopts the eight
draft record types and adds policy, actor authority, capacity, readiness, review
attempt, initial judgment, advice exposure, execution attempt, cohort, experiment
and inspection records. The original draft schema remains a test fixture.
The evidence layer additionally defines claim reviews, deletion tombstones and
reviewed lineage edges, upstream evaluation snapshots and fixed cost policies.
Reviewed deadlines, resource actions, readiness requests, inspection cases and
comparison assessments/policies, cohort inclusion/follow-up, benchmark manifests,
portfolio controls, documentation experiments and actual-cost reviews bring this adoption
to 43 record types; no
operational CLI has initialized real records under an earlier draft contract.

All records carry a producer reference and creation time. Observation, event,
effective and expiry clocks retain separate meanings. Timestamps require a known
UTC offset and at most six fractional digits. Monetary amounts use integer EUR
cents; cash deltas may be negative. Unknown amounts remain null. Floating point
numbers, unknown keys, duplicate JSON keys and unknown record types are rejected.
Operating reviews bind the capture, authored review and upstream result digests.
An immutable human decision never gains a mutable execution-receipt field.

`read_record` validates shape and internal consistency. It cannot establish truth,
source access, competence or human authority. `resolve_references` checks immediate
record identities, types, times and synthetic boundaries. It does not authenticate
an actor or recursively verify retained evidence. A review attempt identifies a
case/actor/packet assessment; judgments and advice exposures reference it. External
execution attempts have a separate intent grouping identity. Labels such as
allocation-rule names are policy text, not implicit evidence references.

Material claims may bind an exact subject record digest and the fields reviewed.
Source permission checks require this binding to cover the requested action and
all source scope fields. Price candidates require reviewed transaction, amount,
unit, currency, country, condition and event-identity fields. A stale verifier
result cannot qualify a different same-ID claim. Missing subject bindings remain
unresolved, not inferred from a free-text statement.

## Evidence services

`sources.assess_source` keeps collection, retention and external transfer separate.
`collector_adapter.dispatch` is an opt-in host callable boundary that rechecks
the explicitly selected rule for each action. No collector or network capability
is enabled automatically. Manual retention also requires its own permission.

`claims.verify_claim` checks current file availability and bound substantive
reviews. It preserves review limitations and contradictory findings. File checks
use an open handle, final handle path and identities before/after reading, with
link/reparse rejection. Qualified handle-path checking currently supports Windows
and Linux with `/proc`; other hosts return unresolved. A tombstone marks a file's
deletion independently of which section a claim cites. A digest is never enough
to establish ownership, cost, functionality or legal applicability.

`events.event_view` reproduces views by observation/event time, and propagates a
refund across copies of the same underlying transaction. `evidence_adapter`
returns nonbinding price candidates, with rejected inputs and reasons; it does not
seal or submit evaluator comparables. Asking prices and awards cannot become
actual sale prices. A reported sale retains that status rather than claiming bank
settlement. `lineage.group_events` distinguishes event copies from later resales,
keeps disputed overlap out of independent counts, and freezes grouping digests.

These services require explicit qualified material/reviewer callbacks for positive
decisions. There is no default trusted actor string or installed substantive
reviewer service. Synthetic test callbacks qualify only the tests. Readiness binds
accepted upstream comparable identities/prices to qualified event companions and
a reviewed mapping; it does not manufacture or seal independent valuation work.
The modules do not constitute an enabled live collection workflow.

## Fixed operating scenarios

`upstream.evaluate_bound` calls the existing retained-evidence evaluator unchanged
and stores original record/review hashes, the exact structured result and policy
digests, and retained-input hashes. `capture_sha256` specifically hashes the
original `record.json`; gallery and comparison files are separately listed in
`retained_inputs`. Snapshots record actual creation time independently of the
requested as-of view. `upstream.recheck` repeats the evaluation and compares all
bound inputs/results. Legacy replay is never a current operational input.
The captured original values independently satisfy the source-hash, policy,
identity and retained-file checks. Their pure gate result must equal the service
result, so temporary path changes cannot bind different arithmetic to the original
review. A later same-day scenario view must follow snapshot creation and remain
within the explicitly reviewed policy validity interval.

`economics.assess_scenario` separates cash-margin previews, full contribution and
liquidity reserves. Unknown material costs withhold a resolved contribution; a
numeric preview remains conditional and never grants purchasing authority. Primary
receipt/acquisition assumptions and every cost require a substantively reviewed
subject binding, including explicit zero amounts. Forecasts retain assumptions.
Non-overlapping underlying exposure IDs prevent paid costs being deducted again
as noncash allocations. Net receipts identify the cash costs already withheld.

Fees/tax use reviewed, dated, account-specific `cost_policy` records supporting
fixed amounts only. Unknown, expired, mismatched or unsupported formulae block a
resolved scenario and maximum-acquisition calculation. Fixed-policy sensitivity
changes only the stated receipt assumption; it is not a valuation guarantee or a
general tax engine. Capacity, authenticated approval and route eligibility remain
separate prerequisites.
The operating policy itself needs a substantive subject binding. Realized
accounting uses the separate `realized_cost_review`/cohort path; changing a forecast
label to `realized` does not turn estimates into observed results.

## Transaction routes and deadlines

`routes.assess_route` binds item, buyer/seller identity references, payment
destination digest, price, delivery, terms and intervention plan. Ownership,
applicability, inspection capability and manually reviewed deadlines remain
independent checks. Required unavailable protection fails regardless of provider
name or financial margin. A changed material route needs new claim bindings.
Documented benign mismatches can be reviewed; unexplained contradictions block.

`deadlines.assess_deadline` consumes reviewed effective dates and trigger claims.
It does not calculate legal periods or infer a deadline from a platform name.
The assessment retains nested claim and verifier digests, including trigger-review
provenance, so later readiness can detect changed evidence. Missing evidence,
authority, expiry or qualified verification never defaults to eligible.

## Cash, time and storage

`capacity.reserve` checks the complete cash/time/storage request under one SQLite
writer transaction. A stale revision, unknown material balance, insufficient
resource or missing qualified evidence refuses the whole hold. Generic journal
imports cannot insert resource records. Full-payload retries return the original
receipt, including after expiry; they do not reactivate a hold.

The reviewed statement names external holds separately from this ledger's internal
reservations. Cleared cash subtracts obligations, floor, incremental stress and
both kinds of hold. Work budgets name a period and subtract recorded use in that
period; late work names its original period. Storage units remain occupied until
an evidenced vacate action. Tentative holds expire; committed liabilities survive
expiry and work-period rollover. Commitments and closeouts require a current
revision, and commitment rechecks the reviewed capacity. No transition authorizes
a purchase or performs a bank action.

`reconciliation` records explicit statements and cash movements in the same journal.
Stable cash IDs make statement-first and event-first arrival count a debit once.
Held payouts are receivables, and never increase cleared cash. Consuming a cash
allocation reduces the cash stock and remaining hold together; a previously
recorded matching debit can consume its allocation once. Reviewed adverse facts
and overruns remain recorded even when they create a negative balance. Such a
deficit blocks new holds. Unknown amounts stay unknown. Corrections use distinct
audited cash IDs; statement reconciliation cannot forget IDs already reflected.
An unmatched movement at or before the statement time requires explicit
reconciliation. New intake rechecks proof for unreflected cash and retained
resource actions; unavailable proof leaves the history and numeric preview intact
but withholds qualified capacity. Policy review dependencies are retained too.

The resource projection can be rebuilt from immutable events. Status derives its
view from those events without writing a cache. These are local software checks;
synthetic reviewer callbacks establish no real bank balance or operating capacity.

## Readiness and local human decisions

`readiness.compose` implements six independent checks: rights, integrity, current
upstream economics, full operating scenario, transaction route and resources.
Any failure blocks the headline while retaining concurrent unknown reasons. Any
remaining unknown gives unresolved. All pass means **ready for human review**;
every computed result retains `purchase_authorized=false`.

`service.assess` recomputes these checks from a reviewed `readiness_request`, the
local journal and retained evaluator inputs. Accepted comparable identities/prices
must map to qualified independent event companions. The request's substantive
mapping and resource requirements need reviewed evidence. New known refunds,
copies and related lineage cannot be hidden by the frozen event list. Later
operative policy, scenario, route, source permission or actor changes require
reassessment. Snapshots bind record and verifier digests, scope, actor and hold;
freshness includes nested proof/deadline expiry and the current upstream day.
`service.prepare` retains that computed companion with an idempotent receipt.

`decisions.authenticate` probes the Windows process user token or POSIX effective
UID and verifies an explicit private scoped actor grant. Environment labels and
caller-supplied actor strings are insufficient. The context is bound to the local
process and is rechecked at decision/preflight time. This is accountability under
the trusted-host assumption, not a sandbox against code running as that same user.
Worker separation requires a separately qualified executor, which is not enabled.

`decisions.decide` retains approve, decline or defer with the same actor's judgment,
packet attempt and exposure history. Approval requires current bound readiness.
`preflight` rechecks approval, human review context and all mutable readiness
inputs, then commits a local attempt and the hold transition in one transaction.
It performs no external action. A preflight receipt means a recorded commitment,
not a completed purchase. The same intent/decision cannot launch a new attempt
under another key. A later decline, defer or approval displaces an older approval
for that actor, case and scope. Expiry is checked again when checks finish, before
the local commitment is recorded. Unknown external outcome preserves the liability and blocks
release until reviewed reconciliation. Proven failure before action and confirmed
execution are separate evidenced outcomes; closing storage still needs vacating.

Readiness snapshots, human decisions and execution attempts cannot enter through
generic journal imports. Local history is immutable; corrections and outcomes are
new records. No buy, pay, contact, publish, dispose or scheduling API is provided.

## Inspection and comparable transfer

`inspection.assess_inspection` evaluates a proposed business-desktop protocol.
Exact expected/observed configuration, tools, inspector scope, access, management
and activation release all need reviewed evidence. Every required function check
must be observed and supported; extra failed observations also remain visible.
Power-on alone, a seller's diagnosis, or a repair forecast cannot establish current
working condition. The packaged `resources/desktop_protocol.md` describes the
initial scope. The engine executes no inspection, repair or sanitization action.

`comparability.assess_comparisons` keeps the target's deliverable offer separate
from each source's observed service bundle. Every supplied event receives an
explicit reviewed transfer argument and included/excluded/disputed/unresolved
disposition. Unknown service fields remain unknown. Fixed signed-cent adjustments
require reviewed, current policies scoped to the case, offer and event; no
automatic warranty/reputation premium or percentage discount is applied.
Referenced terms, testing and reputation claims are separately resolved and
verified for the current view; changed or unavailable service evidence invalidates
the comparison binding rather than being treated as an opaque label.

`sensitivity.compare_sensitivity` removes one whole material event at a time,
including all its duplicate copies. Conflicting same-event prices remain
unresolved. Assumption changes also require scoped reviewed fixed policies.
Displayed ranges describe those conditional scenarios; they are not confidence
intervals or promises about a sale. Changed condition or service requires a new
assessment binding, while the original source records and valuation remain intact.

## Consecutive leads and observed outcomes

`cohorts.include` retains unique lead/case inclusion receipts against a frozen
cohort definition and observation window. Local inclusion positions remain
consecutive; this does not establish completeness of an external source feed.
Every recorded inclusion appears in reports, including unavailable leads,
non-purchases and missing outcomes. Source-frame completeness needs independent
review. A challenged subset is not a natural prevalence estimate.

`outcomes` separates physical and financial states and keeps append-only
corrections. Actual operator time and assigned cash costs remain with rejected
leads. Cleared cash joins by exact resource-ledger cash IDs, states and amounts,
with current proof; equal amounts alone do not match. Duplicate allocations stay
unresolved. A delivered item can have a held payout without increasing cleared
cash. Refunds and returns retain their different financial/physical meanings.

`cohorts.report` and `export_report` produce private as-of views. Explicit reviewed
follow-up covers physical closure, financial closure and aftercare, and cannot
close live commitments or held payouts. Closure binds exact current outcome and
review material; backdated additions and proof changes require new follow-up.
Known payout IDs need qualified allocations when clearing, and allocations are
unique across cohorts sharing an account. Missing operator time keeps measurement
follow-up incomplete. Later outcomes reopen follow-up. Missing,
incomplete and censored follow-up stay visible; open stock has no fabricated
zero-price sale. Observed cash flow stays separate from realized contribution.

A `realized_cost_review` must bind current outcome material, completed follow-up,
actual operator time and every allocated cleared cash ID. Acquisition cost is
explicitly reconciled or reviewed as zero; unknown is not zero. Required actual
cost categories need fixed reviewed costs or explicit evidenced zero, with dated
account-specific fee/tax policies. Reserve-only categories do not establish actual
cost completeness. Overlapping exposure allocations are rejected.

The realized calculation subtracts noncash costs from reconciled case cash flow.
Cash expenses and net-withheld fees are not deducted twice; reserves are disclosed
separately. The review cutoff must cover the cash, observations and follow-up it
uses. Later changed material requires a new accounting review. The cohort total
stays null if any included case lacks qualified contribution, with known subtotals
and missing-case counts exposed. This fixed-cost accounting does not establish
profitable operation or replace a qualified accounting/tax determination.

## Independent benchmark tooling

`benchmark.freeze` binds method/protocol versions, exact input/label digests,
independent reviewer assignments and grouped partitions before prediction imports.
Source rights and reviewer grants need substantive verification. An assessed method
cannot provide its own ground truth. Copies, related records and exposure propagate
through leakage groups; future inputs and contaminated groups remain audited
outside blind scoring. Label corrections, later source revisions and new lineage
discoveries withdraw stale comparisons.

`benchmark.score` reports explicit counts and integer ratios for false support,
coverage and missing adjudication, with severity counts. Empty denominators are
null. Challenge cases do not estimate natural prevalence; prospective inference
still needs independent sampling-frame review. No real labels or model calls are
supplied by this tooling.

## Portfolio and human pause review

`portfolio.status` groups reviewed provider/account/source/category/correlation
exposures, preserving unknown membership. Shared cash holds stay in the capacity
ledger. Unknown manual attempts, occupied storage and open outcomes remain visible
after an intake pause or reporting cutoff.

`portfolio.record_decision` requires an authenticated local context with a reviewed
`portfolio_control:<scope>` grant. The protected writer stamps actual recording
time and retains the original review request separately. Resume needs current
resources and reviewed material. Pause blocks new reservations and manual preflight;
it does not release resources or stop aftercare. Actual reconciliation and storage
vacating remain separate ledger actions.

`exit_review.assess` compares reviewed future receipts, costs, work, storage and
delay. Sunk acquisition cost affects the historical-inclusive scenario, not the
incremental ordering. These are conditional scenarios, not realized profits or
permission to sell, repair, return or dispose of an item.

## Truthful documentation experiments

`offers.render` prepares private Markdown drafts with the same substantive facts
in plain and structured formats. Ownership and inspection need bound review. All
reported faults and mandatory disclosures remain visible; a passed check needs
current inspection prerequisites and observed evidence.

`experiments.documentation.assign` balances a frozen seeded physical-item frame
within matched blocks. Prices, services and qualified completed testing coverage
stay fixed. Each physical item and outcome case receives at most one assignment
and must first enter its cohort. Reports preserve missing observations, inquiries,
handling/support time and unfinished outcomes. Price/service changes invalidate
the documentation-only contrast and retain deviations. Views are not the primary
endpoint. Reconciled contribution uses qualified full-cost cohort results only.
No experiment function publishes an offer or contacts a party.

## Explicit policy

`operations.config.read_policy(path)` reads only the explicitly selected TOML file.
It never discovers or merges `resale.toml`. The document has `schema_version = 1`
and a `[policy]` table containing an `operating_policy` record. Quote timestamps as
strings. TOML cannot express null, so omitted nullable settings become null.
Required arrays are explicit; empty authority arrays remain unresolved.

`policy_snapshot` returns an independent complete record and its digest. Missing
floor, labor valuation, source authority or deadline rules are unresolved. Presence
and expiry checks cannot grant source rights. Synthetic examples never fill real
policy gaps. Operational records and policy belong in private local storage and
are excluded from release inputs.

## Local journal and recovery

`operations.store.initialize(new_directory)` explicitly creates a private directory
and `operations.sqlite3`. Existing directories are refused. On Windows the new
directory receives access for the authenticated user SID and SYSTEM; on POSIX it
is created with mode 0700. Links/reparse paths and Windows network drives are
refused. POSIX network mounts have not been qualified: use a local filesystem.
This is a trusted-host boundary; a host administrator can rewrite history and
recompute hashes. It is not a multiuser authorization system.

`append(database, record, request_key=...)` serializes record, event, receipt and
projection changes in one short SQLite transaction. Identical retries return the
original receipt. Changed payloads under the same key fail. A new key cannot reuse
a record identity. Busy writers fail after a bounded timeout. No external call or
human wait occurs inside the transaction. `pilot-state.sqlite3` is untouched.

Canonical record bytes are UTF-8 JSON with sorted keys and separators `,` and `:`.
Digests prepend `resale.operations/<domain>/v1` and a NUL byte. Original evidence
hashes remain separate. SQL triggers reject updates/deletions of journal facts;
the integrity scanner checks hashes, sequence, receipt bindings and projections.

Case projections report **known cash-event deltas**, explicit counts of unknown
amounts/time, and latest states by event time. They are not bank balances or
available cash. Corrections append a new outcome referencing one prior outcome in
the same case/cohort; the original remains readable. Synthetic and real histories
cannot be combined under a case identity. Resource views use their separate
reviewed statement and cash-ID reconciliation within the same journal.

`status`, `get_record`, `integrity_scan` and `migration_status` use read-only
connections. Missing stores stay missing. Unknown schema versions are refused for
writes; there is no migration from an unrelated queue database. Read-only status
derives from retained events, so a deleted cache cannot produce a false balance.
`rebuild_projections` explicitly reconstructs caches without changing facts.

Only rollback-journal SQLite stores are supported. WAL is rejected before opening
SQLite because a read-only WAL connection can create sidecar files. An interrupted
rollback journal requires explicit `recover` before inspection; status never performs
recovery implicitly. Recovery retains committed events and discards only SQLite's
uncommitted transaction, then rebuilds the derived projections.

Exports are derived after commit into new files. An export failure does not undo
the journal transaction; reconcile/retry the same request key and export again to
a new path. `backup` uses SQLite's consistent backup API; `restore` copies into a
new private recovery directory. Neither can overwrite an existing operating store
or erase commitments recorded after a backup.

All development fixtures are synthetic. Local tests do not establish real source
rights, legal applicability, reliable marketplace valuations or profitable operation.
