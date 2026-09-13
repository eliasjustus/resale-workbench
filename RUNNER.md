# Supervised evidence workflow

This procedure is for a human/operator coordinator. Python persists state and verifies contracts; it does not dispatch models, browse research sources on an agent's behalf, or certify facts. All paths below are local placeholders. Work in a private workspace created by `resale init WORKSPACE` and edit its config before use. Legacy scripts remain available for private historical replay; current manual economics should use the `resale` commands with retained-file binding.

## Start and triage

```text
resale run init RUN --config WORKSPACE/resale.toml --category computers
resale search prepare RUN --url "COPIED_NATIVE_SEARCH_URL" --center Berlin --radius-km 50 --category computers --notes "Describe inspected native category mapping and newest-first ordering"
resale search collect RUN
resale run import-discovery RUN
resale run status RUN
resale run select RUN LISTING_ID
resale run triage RUN OTHER_ID deferred "Missing condition evidence; revisit later"
resale run backlog --config WORKSPACE/resale.toml
```

Initialize only a new authorized run. Its manifest freezes the intended search window, location/radius/categories, role settings, screening policy and preparation budget. Replace the example city/radius/category above with your frozen settings and the URL/notes with values actually inspected in the native browser controls. Use the native city name in configuration. The adapter currently accepts clean HTTPS city/category/radius URLs ending `c<CATEGORY_ID>l<LOCATION_ID>r<KM>`. Category labels are operator-defined mappings, not automatic platform IDs.

Omit RUN on `resale run init --config ...` to create a timestamped folder beneath the config's workspace; the output includes its full run_path. With one configured category it is inferred, otherwise pass --category. Explicit run paths resolve against the current directory. `resale run import-discovery RUN` uses the run's discovery folder; --discovery-dir or the existing --rows/--summary pair supplies alternate input. `resale run status RUN` is an offline inspection of state and prerequisites, not an instruction to dispatch a prepared job again.

Preparation records `search-plan.json` and binds it to the state database. Missing/changed plans, changed manifests and contradictory city/radius settings fail before browsing. A legacy `search-browser-check.json` alone is insufficient for new collection. Preserve old runs; create a new configured run for the current setup procedure. Each listing still needs substantive category/location inspection. Search rows contain observed IDs/URLs, rendered posting metadata and observation times. `eligibility` classifies time only. Coverage stays bounded/incomplete unless observed evidence proves otherwise.

A browser startup failure can be retried using the same command; an empty discovery folder is accepted. Once files have been retained, preserve them and use a new run for another collection attempt. Even a blocked/zero-row completed attempt records both rows.json and summary.json; inspect its stop_reason and do not represent it as complete coverage. Concurrent collection is rejected by a per-run discovery.lock directory, removed on normal exit/failure. After a process is forcibly terminated, remove that empty lock only after confirming no collector remains active. Never clear it to start a second collector.

Selection records whole listings. Unselected rows remain unreviewed. Triage rejection and deferral are separate from economic outcomes. Use `select --carryover` only for an unclaimed deferred lead; recheck availability and preserve its prior-run origin. Do not rewrite posting times or treat old leads as fresh discoveries. Selection freezes when valuation begins.

Configured preparation time limits new selection/job preparation. Optional count caps apply only when supplied. Existing jobs can still be reconciled, and valuation/closeout can continue. The coordinator separately bounds actual external research time, token/cash use, corrections and cancellation. Do not repeatedly prepare or redispatch a job to bypass the budget.

## Inspect and bind a private target

Collect through an authorized source path, or use a retained complete capture. Partial/gone/blocked captures must not be represented as complete evidence. Before binding, make and review the privacy copy described in README.md and PRIVACY.md:

```text
resale privacy-workspace CAPTURE EDITABLE
resale privacy-template CAPTURE EDITABLE --output privacy-review.json
resale run bind RUN LISTING_ID CAPTURE --sanitized EDITABLE --privacy-review privacy-review.json
```

Between the first and second command, edit the copied text/photos if needed; after the second, inspect and complete the review declarations. Pending templates cannot be bound. This path creates the reviewed model input inside the run and stores private audit notes in a sibling file. Source evidence remains unchanged. The legacy `bind RUN ID CAPTURE` path produces a price-masked unreviewed draft and still requires an actual independent privacy inspection before attestation.

Create checks.json with `actor`, actual timezone-aware `checked_at`, specific `evidence_notes`, all five true declarations (`source_verified`, `posting_time_verified`, `radius_verified`, `price_mask_checked`, `privacy_checked`), `source_capture_sha256`, `target_input_sha256` from bind output, and `opened_photo_indices` covering every image. A true flag records actual inspection; never set it simply to advance the queue.

```text
resale run attest RUN LISTING_ID checks.json
resale run job RUN LISTING_ID reviewer
```

The job JSON contains the requested model/effort, input/output directories and frozen playbook. Its folders and prompts are private. Verify the external worker supports the selected model/tools and intended data handling. Give it a fresh context and only the assigned files. Procedural instructions do not create an OS sandbox; use an actual restricted worker environment when required. Do not pass original captures or private audit siblings merely because they are nearby on disk.

## Execute and reconcile roles

Dispatch the prepared job once through the chosen manual/external executor, then register its actual returned identity:

```text
resale run dispatched RUN LISTING_ID reviewer ACTUAL_WORKER_ID
resale run complete-review RUN LISTING_ID
resale run job RUN LISTING_ID valuator
resale run dispatched RUN LISTING_ID valuator ACTUAL_WORKER_ID
resale run complete-valuation RUN LISTING_ID
resale run report RUN
```

These commands bracket real work; they are not a script that performs research automatically. Repeated job requests return existing work for reconciliation, not permission to redispatch. All selected preparation jobs must finish, fail or be explicitly stopped before any valuation job starts. A zero-transaction evidence packet can still advance for adequacy assessment. A blocked source is not proof that no market exists.

Preparation follows the packaged REVIEWER playbook (handoff schema 2). Retain original sources with evidence kind, locator, actual capture time, facts supported and limitations. Inspect input/returned research for personal data before transferring it to another worker. If research must be redacted for transfer, preserve its private original and document the derivative and evidential limitations; do not silently alter a READY-covered packet. Source privacy is an operator responsibility, not an automatic guarantee of the capture helper.

Valuation follows the VALUATOR playbook (schema 2), independently checking retained evidence, material comparison sensitivity and condition/repair uncertainty. Model/effort declarations must match the frozen job. The worker receives no acquisition costs and cannot establish profitable purchase support. It may estimate conditionally or abstain. Hash checks do not verify actual visual inspection, source truth or matching condition.

Preserve first attempts before corrections. A stopped running worker must have observed terminal status before `stop-agent` is used. Reconcile interrupted dispatches explicitly. Do not edit shared code or frozen instructions to rescue a live case.

## Apply the economic gate

Create `resale draft-review CAPTURE --run RUN --output economic-draft.json` and fill the factual review/cost inputs after substantive inspection. Keep original source references in a private evidence directory. Comparables must be unique actual single-unit transactions, within the frozen country/currency/recency policy and matching the target's current condition. Retain `kind`, `evidence_id`, `locator`, timezone-aware `captured_at`, `source_paths`, actual sold date/price, country, units, review evidence and limitations. Asking context, modelled estimates and expert opinions cannot satisfy transaction support.

Provide explicit transaction adequacy reasoning covering exactly the accepted records. Unknown costs or unresolved repairs remain unresolved. Work requires evidenced materials and a positive additional allowance. Diagnosis and available equipment cannot be inferred from a seller's claimed easy fix.

```text
resale seal-review CAPTURE economic-draft.json --evidence-root EVIDENCE --output economic-sealed.json
resale evaluate CAPTURE economic-sealed.json --run RUN --output economic-result.json
```

The separate economic JSON is the evidence/cost screen. The pilot queue records preparation/valuation stages and their outcomes; it does not automatically promote its stage outcome to economic support from this separate result. Closeout should link the result and distinguish valuation from economics, deferred from unreviewed, and conditional estimates from supported screens. Purchases always require a human decision.
