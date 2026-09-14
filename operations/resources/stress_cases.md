# Offline operational stress protocol

All fixtures and claim-verification callbacks are synthetic. No test collects
listings, invokes a model, contacts a party or executes a transaction.

The readiness pair holds the retained evaluator, economics, resource request and
reviewed route evidence constant. One route fact changes: required protection is
unavailable, or item/party/payment binding is unresolved. Positive economics does
not override either condition; the complete benign case reaches human review.

The shared-account rehearsal commits two cases, adds a held payout, freezes only
the reviewed affected cash, consumes part of a commitment and records a return
debit. Neither a projected margin nor a held payout supplies cleared funds.
Deficits stop new reservations while retaining the existing liabilities.

Actual processes race commitment against cancellation and crash before/after
reservation commit. A per-connection SQLite page limit produces SQLITE_FULL in
a temporary database without filling the host disk. Refused writes produce no
receipt or partial resource hold; recovery/retry preserves at-most-once records.
The capacity suite separately races two processes for the last three resources.

An unknown manual attempt and open claim survive an intake stop caused by unknown
capacity, expiry and an ended reporting horizon. This is a conservative intake
stop. The portfolio layer also records an authenticated dated human pause in the
unknown-action rehearsal; preflight tests reject approval displaced by that pause.
Existing commitments and aftercare still require explicit
reconciliation; these rehearsals do not establish live host isolation, evidence
truth or profitable operation.
