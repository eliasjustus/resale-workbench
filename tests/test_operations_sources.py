"""Synthetic action spies prove missing authority prevents dispatch."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from operations.collector_adapter import dispatch
from operations.errors import UnresolvedError
from operations.contracts import record_digest
from operations.sources import assess_source, SCOPE_FIELDS

RECORDS = json.loads((Path(__file__).parent / "fixtures/operations-records.json").read_text(encoding="utf-8"))


class SourceRightsTests(unittest.TestCase):
    def setUp(self):
        self.rule = dict(deepcopy(RECORDS["source_rule"]), collection="allowed", retention="allowed", basis_refs=["written-authority"])
        self.claim = dict(deepcopy(RECORDS["material_claim"]), record_id="written-authority", claim_type="legal_applicability")
        self.claim["subject"] = {"record_id":self.rule["record_id"],"record_type":"source_rule","record_sha256":record_digest(self.rule),"verified_fields":[*SCOPE_FIELDS,"collection","retention"]}
        self.context = {key:self.rule[key] for key in SCOPE_FIELDS}
        self.inputs = dict(action="collection", context=self.context, as_of="2026-01-01T12:30:00Z", synthetic=True, resolve={"written-authority":self.claim}.get)
        self.calls = []

    def verified_fixture(self, identity, **kwargs):
        # A synthetic stub for the future retained-authority service, not live proof.
        return {"state":"pass", "reason_codes":[],"claim_id":identity,"claim_sha256":record_digest(self.claim),"subject":self.claim["subject"]}

    def test_missing_verifier_or_written_authority_never_dispatches(self):
        for rule in (None, self.rule, dict(self.rule, basis_refs=[])):
            with self.assertRaises(UnresolvedError):
                dispatch(lambda:self.calls.append("network"), rule=rule, **self.inputs)
        self.assertEqual(self.calls, [])
        result = assess_source(self.rule, **self.inputs)
        self.assertIn("material_authority_verifier_unavailable", result["reason_codes"])

    def test_permission_is_action_specific_and_rechecked_after_expiry_or_conflict(self):
        dispatch(lambda:self.calls.append("collection"), rule=self.rule, verify_claim=self.verified_fixture, **self.inputs)
        self.assertEqual(self.calls, ["collection"])
        for extra in ({"action":"external_transfer"}, {"as_of":"2026-01-02T12:00:00Z"}):
            with self.assertRaises(UnresolvedError):
                dispatch(lambda:self.calls.append("forbidden"), rule=self.rule, verify_claim=self.verified_fixture, **dict(self.inputs, **extra))
        for state in ("fail", "unknown"):
            result = assess_source(self.rule, verify_claim=lambda *args, **kwargs:{"state":state}, **self.inputs)
            self.assertNotEqual(result["state"], "pass")
        self.assertEqual(self.calls, ["collection"])

    def test_role_route_purpose_scope_and_synthetic_identity_are_exact(self):
        for key in SCOPE_FIELDS:
            result = assess_source(self.rule, verify_claim=self.verified_fixture, **dict(self.inputs, context=dict(self.context, **{key:"other"})))
            self.assertEqual(result["status"], "scope_mismatch")
        result = assess_source(self.rule, verify_claim=self.verified_fixture, **dict(self.inputs, synthetic=False))
        self.assertEqual(result["state"], "fail")
        result = assess_source(dict(self.rule, retention="denied"), verify_claim=self.verified_fixture, **dict(self.inputs, action="retention"))
        self.assertEqual(result["status"], "permission_denied")


if __name__ == "__main__":
    unittest.main()
