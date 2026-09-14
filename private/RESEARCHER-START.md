# Start here: isolated research on Resale Workbench

This brief assumes access to a fresh repository checkout and, if available, ordinary public-web research. It does not require the owner's computer, private configuration, marketplace login or earlier conversation. Record the exact commit you inspected. Read-only repository inspection and appropriately scoped public research are sufficient to begin many topics.

## Project in one page

Resale Workbench is an MIT-licensed local tool for collecting and reviewing evidence for human resale decisions. The optional current marketplace adapter is Kleinanzeigen, and current economic policy supports Germany/EUR. The command is `resale`. A person makes every purchase decision; the software does not purchase or contact sellers.

The existing workflow separates discovery, target capture, reviewed/private model input, evidence preparation, valuation and human review. Current role documents are REVIEWER 3.2 and VALUATOR 2.1. Those are specific dispatched roles with narrower input/exposure rules, not a requirement to force every broad research assignment through a valuation role.

The 14 September development adds an offline `operations` package: 43 companion record types, evidence/lineage review, fixed-cost scenarios, routes/deadlines, cash/time/storage accounting, readiness and authenticated local decisions, inspection/comparability, cohorts/actual costs, benchmarks and experiment-related APIs. See current code and PROJECT.md for later changes. At the documented checkpoint, a qualified isolated worker, transfer/exposure integration and operations CLI remained incomplete; live valuation/research-depth/advice-order studies were not executed. Do not propose an already implemented component as wholly missing, or mistake a record/API for a completed live service.

Two retained cases illustrate current evidence limitations: an i7-11700F/RTX 3060 PC at a historical EUR 600 ask and a 2012 seven-seat Berlingo diesel at EUR 1,900 with seller-disclosed AC failure and oil loss. Fresh comparable research improved evidence, but neither case had a supported acquisition. New listing discovery was not rerun. This is not a representative sample or proof of profitability; most discovered listings were not evaluated.

## Reading order

1. [AGENTS.md](../AGENTS.md), [PROJECT.md](../PROJECT.md), [README.md](../README.md): constraints, current implementation and setup/use.
2. [RUNNER.md](../RUNNER.md), [OPERATIONS.md](../OPERATIONS.md), [PRIVACY.md](../PRIVACY.md): actual workflow, implemented contracts and access/exposure boundaries.
3. [Research package index](research/2026-09-14/README.md), especially RESULTS.md, RETROSPECTIVE.md, IMPLEMENTATION-STATUS.md and RESEARCH-QUESTIONS.md: observations, causal limits and outstanding work. COMPARISON.md is the earlier offline checkpoint, not fresh market discovery.
4. Inspect only the source, tests and role documents relevant to the question. Use the map below. If researching publication, read [PUBLISHING.md](../PUBLISHING.md). The local full design catalog and raw evidence are not bundled; do not pretend references to them are available primary evidence.

## Code and evidence map

| Question | Starting points |
|---|---|
| Discovery coverage, time eligibility, browser collection | `collector/discover.py`, `eligibility.py`, `browser.py`, `extract.py`; `tests/test_discovery.py`, `test_search_setup.py` |
| Configuration, frozen runs, queue and status | `pilot/config.py`, `queue.py`, `status.py`, `resale_tool/cli.py`; RUNNER.md |
| Evidence packets, privacy and raw source retention | `evaluation/handoff.py`, `privacy.py`, `research_sources.py`, `browser_capture.mjs`, `gallery.py`; packaged REVIEWER.md |
| Current valuation/economic decisions | `evaluation/service.py`, `manual.py`, `comps.py`, `gates.py`; packaged VALUATOR.md |
| Evidence rights, claims and transaction identity | `operations/sources.py`, `claims.py`, `events.py`, `lineage.py`, `evidence_adapter.py` |
| Costs, reserves, resources, readiness and decisions | `operations/economics.py`, `policies.py`, `capacity.py`, `reconciliation.py`, `readiness.py`, `service.py`, `decisions.py` |
| Device inspection and comparable adjustments | `operations/inspection.py`, `comparability.py`, `sensitivity.py`, `resources/desktop_protocol.md` |
| Experiments, follow-up, actual outcomes and exit | `operations/benchmark.py`, `cohorts.py`, `outcomes.py`, `realized.py`, `portfolio.py`, `exit_review.py`, `experiments/documentation.py` |
| Packaging, verification and public-source boundaries | `release-files.json`, `tools/check_candidate.py`, `tools/stage_release.py`, `.github/workflows/tests.yml` |

Tests use synthetic fixtures and establish bounded software behavior. An authenticated local human identity is not an agent sandbox. A hash establishes byte identity, not truth. A configured CI job is not a successful run. Private report counts and reviews are reported local evidence unless you independently reproduce them.

## How to conduct a useful broad investigation

Start with the decision the research should inform. State the question, scope, current implementation assumptions and a bounded effort budget. If the topic is broad, choose a few high-value uncertainties and explain why. Do useful work with available sources before requesting missing information. Physical inspection for one candidate does not block research on other candidates or software questions.

Use current primary sources for technical, marketplace-policy, cost and legal-context claims; record access date, jurisdiction, account/route assumptions and source limitations. Do not infer a paid sale from a listing disappearing or an accepted-offer asking price. Keep transactions, asking context, model estimates, expert judgment and observations distinct. Retain conflicting findings. Do not turn missing repair, fee, labour or resource costs into zero.

Distinguish project facts, external evidence, inference and proposed changes. Explain what would falsify each material conclusion. Compare simpler alternatives and account for manual/browser effort, supervision and failed attempts. Ask whether an added check changes a useful decision or merely creates another unknown-status record. More architecture and passing tests do not establish improved opportunity yield.

For empirical comparisons, separate listing discovery from fixed-case research and valuation. Record candidate selection, price exposure, model/operator/protocol, category/condition/date controls, original evidence completeness, effort and follow-up. A change in those factors prevents clean attribution to software alone. Keep unsupported, unresolved, deferred and unreviewed outcomes distinct.

Write new outputs in an assigned local output directory. The repository's `private/` name is not protection; do not publish new research automatically. Do not modify originals, start schedules, contact people, purchase, log into services, install isolation infrastructure or publish without the relevant assignment authorization. If blocked, name the exact missing source/capability and continue independent research that remains possible. Do not fabricate access to omitted local evidence.

## Expected deliverable

Provide an executive answer; a concise map of relevant implementation; an evidence table with source/date/claim/limitations; competing explanations or options; ranked recommendations with cost/dependencies and disconfirming tests; and clearly separated unknowns and next actions. State what you inspected, what you tested, and what remains unverified. Link to exact repository paths/commit and external primary sources. Preserve evidence only through authorized tools and within the assigned privacy boundary.

## Copyable assignment

> You are an independent researcher working on Resale Workbench: https://github.com/eliasjustus/resale-workbench. Your task is to investigate **[question or broad theme]** and recommend evidence-backed next steps. Start by reading `private/RESEARCHER-START.md` and follow its reading order; record the commit examined. You have repository context, not the owner's private machine, credentials or omitted raw evidence. Inspect current code and tests before claiming a feature is missing. Separate implemented code, locally verified behavior, actual live execution and proposed work. Use appropriate current primary sources and preserve uncertainty. If no narrower question is supplied, rank the most decision-relevant research gaps in `private/research/2026-09-14/RESEARCH-QUESTIONS.md` and investigate the most promising unblocked ones within a stated budget. Work autonomously on read-only inspection and public research; no seller contact, purchases, scheduling, account changes or publication. Return a source-backed report, competing explanations, ranked recommendations, falsifiable validation steps and precise blockers. Do not claim software improves discovery, valuation or profit without suitable comparative evidence.
