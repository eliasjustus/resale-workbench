"""Synthetic prices and reversals preserve historical as-of distinctions."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from operations.evidence_adapter import adapt_events, PRICE_FIELDS
from operations.contracts import record_digest
from operations.events import event_view

RECORDS = json.loads((Path(__file__).parent / "fixtures/operations-records.json").read_text(encoding="utf-8"))


class EvidenceEventTests(unittest.TestCase):
    def setUp(self):
        self.sale = dict(deepcopy(RECORDS["evidence_event"]), record_id="sale", event_id="transaction-one", transaction_state="sold_reported", price_basis="actual_sold_item_price", item_price_cents=12500, units=1, seller_country="DE", lineage_status="established", event_at="2026-01-01T11:00:00Z", material_claim_ids=["price-claim"])
        self.inputs = dict(as_of="2026-01-01T12:30:00Z", synthetic=True, verify_claim=lambda identity, **kwargs:{"state":"pass", "claim_id":identity, "subject":{"record_id":"sale", "record_type":"evidence_event", "record_sha256":record_digest(self.sale), "verified_fields":sorted(PRICE_FIELDS)}})

    def test_asking_and_award_prices_never_become_sales(self):
        for state, kind in (("not_applicable","asking_context"), ("awarded","transaction")):
            result = adapt_events([dict(self.sale, transaction_state=state, kind=kind)], **self.inputs)
            self.assertEqual(result["candidates"], [])
            self.assertTrue(result["rejected"][0]["reason_codes"])
        result = adapt_events([self.sale], **self.inputs)
        self.assertEqual(result["candidates"][0]["transaction_state"], "sold_reported")
        self.assertEqual(result["candidates"][0]["source_label_verbatim"], self.sale["source_label_verbatim"])

    def test_future_event_or_acquired_support_cannot_enter_historical_view(self):
        for updates in ({"created_at":"2026-01-02T12:00:00Z", "observed_at":"2026-01-02T12:00:00Z"}, {"created_at":"2026-01-02T12:00:00Z", "observed_at":"2026-01-02T12:00:00Z", "event_at":"2026-01-02T11:00:00Z"}):
            result = adapt_events([dict(self.sale, **updates)], **self.inputs)
            self.assertEqual(result["candidates"], [])
            self.assertIn("look_ahead_evidence", result["rejected"][0]["reason_codes"])

    def test_unknown_units_aggregate_and_hidden_price_stay_excluded(self):
        for updates in ({"units":None}, {"units":2}, {"price_basis":"aggregate_average"}, {"item_price_cents":None}, {"material_claim_ids":[]}):
            result = adapt_events([dict(self.sale, **updates)], **self.inputs)
            self.assertEqual(result["candidates"], [])
        result = adapt_events([self.sale], **dict(self.inputs, verify_claim=None))
        self.assertEqual(result["candidates"], [])
        for verifier in (lambda *args,**kwargs:{"state":"pass"},lambda *args,**kwargs:{"state":"pass","subject":{"record_id":"other","record_type":"evidence_event","record_sha256":record_digest(self.sale),"verified_fields":sorted(PRICE_FIELDS)}}):
            self.assertEqual(adapt_events([self.sale],**dict(self.inputs,verify_claim=verifier))["candidates"],[])

    def test_refund_changes_current_view_without_rewriting_prior_label_or_view(self):
        refund = dict(self.sale, record_id="refund", transaction_state="refunded", created_at="2026-01-02T12:00:00Z", observed_at="2026-01-02T12:00:00Z", event_at="2026-01-02T11:00:00Z", related_event_ids=["sale"], source_label_verbatim="synthetic refund reported")
        original = deepcopy([self.sale, refund])
        past = adapt_events(original, **self.inputs)
        current = adapt_events(original, **dict(self.inputs, as_of="2026-01-02T12:30:00Z"))
        self.assertEqual(len(past["candidates"]), 1)
        self.assertEqual(current["candidates"], [])
        self.assertIn("later_reversal_requires_review", current["rejected"][0]["reason_codes"])
        self.assertEqual(adapt_events(original, **self.inputs), past)
        self.assertEqual(original, [self.sale, refund])

    def test_refund_applies_to_other_copies_of_the_underlying_transaction(self):
        copy = dict(self.sale, record_id="copy", material_claim_ids=["copy-claim"])
        refund = dict(self.sale,record_id="refund",transaction_state="refunded",related_event_ids=["sale"])
        by_claim = {"price-claim":self.sale,"copy-claim":copy}
        def verified(identity, **kwargs):
            record = by_claim[identity]
            return {"state":"pass","claim_id":identity,"subject":{"record_id":record["record_id"],"record_type":"evidence_event","record_sha256":record_digest(record),"verified_fields":sorted(PRICE_FIELDS)}}
        result = adapt_events([self.sale,copy,refund],**dict(self.inputs,verify_claim=verified))
        self.assertEqual(result["candidates"],[])
        self.assertIn("later_reversal_requires_review",next(item for item in result["rejected"] if item["record_id"]=="copy")["reason_codes"])


if __name__ == "__main__":
    unittest.main()
