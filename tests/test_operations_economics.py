"""Invented fixed-cost scenarios with actual unchanged upstream service calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from operations.contracts import record_digest
from operations.economics import assess_scenario
from operations.errors import ContractError, IntegrityError, UnresolvedError
from operations.upstream import evaluate_bound, read_result, recheck
from evaluation.service import evaluate_capture
from resale_tool.demo import sample, write_json, NOTICE

RECORDS = json.loads((Path(__file__).parent / "fixtures/operations-records.json").read_text(encoding="utf-8"))


class OperatingEconomicsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        record, review = sample("synthetic-economic-case",200)
        for index,comp in enumerate(review["comps"]):
            comp["item_price_eur"] = str(330+index*10)
        write_json(self.root/"record.json",record)
        review["source_record_sha256"] = hashlib.sha256((self.root/"record.json").read_bytes()).hexdigest()
        write_json(self.root/"review.json",review)
        (self.root/"synthetic-evidence.txt").write_text(NOTICE+" Invented transactions EUR330 and EUR340.",encoding="utf-8")
        self.as_of = "2026-01-15T12:30:00Z"
        self.clock = patch("operations.upstream.utc_now",return_value=self.as_of)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.upstream = evaluate_bound(self.root,self.root/"review.json",record_id="upstream",case_id="case",producer_ref="synthetic-actor",as_of=self.as_of,synthetic=True)
        self.policy = dict(deepcopy(RECORDS["operating_policy"]),record_id="policy",scope="synthetic-account",valid_until="2026-02-01T12:00:00Z",authority_refs=["synthetic-actor"],source_authority_refs=["synthetic-source"],cash_floor_cents=0,incremental_stress_cents=0,labor_cents_per_hour=2000,minimum_contribution_cents=2000,cost_coverage=["transport","labor"],contribution_rule="receipts_minus_all_costs",labor_allocation_rule="reviewed fixed workload",research_allocation_rule="reviewed allocation",deadline_policy="manual reviewed deadlines",clock_skew_seconds=0,validity_seconds=3600)
        self.review = dict(deepcopy(RECORDS["operating_review"]),record_id="scenario",case_id="case",scope="synthetic-account",created_at=self.as_of,as_of=self.as_of,upstream_evaluation_id="upstream",upstream_result_sha256=self.upstream["result_sha256"],capture_sha256=self.upstream["capture_sha256"],review_sha256=self.upstream["review_sha256"],policy_id="policy",item_receipt_cents=33000,shipping_collected_cents=0,acquisition_cents=20000,scenario_status="complete",basis_claim_ids=["scenario-basis"],assumptions=["Invented forecast; fixed receipt assumption is not a guaranteed floor"],cost_lines=[self.line("transport",5000,"cash"),self.line("labor",6000,"noncash")])
        self.cost_policies = {}
        self.policy["basis_claim_ids"] = ["operating-policy-basis"]

    def line(self,name,amount,treatment,**updates):
        return dict(exposure_id=name,category=name,amount_cents=amount,treatment=treatment,basis_kind="synthetic",basis_claim_ids=[name+"-basis"],incurred_at=None,effective_until=None,allocation_rule_id="one-case",cost_policy_id=None,underlying_exposure_ids=[name],**updates)

    def verifier(self,identity,**kwargs):
        if identity in self.policy["basis_claim_ids"]:
            return {"state":"pass","claim_id":identity,"subject":{"record_id":self.policy["record_id"],"record_type":"operating_policy","record_sha256":record_digest(self.policy),"verified_fields":list(self.policy)}}
        policy = next((value for value in self.cost_policies.values() if identity in value["basis_claim_ids"]),None)
        target = policy or self.review
        fields = ("account_scope","category","rule","amount_cents","effective_at","valid_until") if policy else ("cost_lines","item_receipt_cents","shipping_collected_cents","acquisition_cents","receipts_basis","included_cost_exposure_ids","scenario_kind","scope")
        return {"state":"pass","claim_id":identity,"subject":{"record_id":target["record_id"],"record_type":target["record_type"],"record_sha256":record_digest(target),"verified_fields":list(fields)}}

    def assess(self,**options):
        return assess_scenario(self.review,self.policy,self.upstream,resolve=self.cost_policies.get,verify_claim=self.verifier,**options)

    def test_cash_and_full_contribution_are_distinct_fixed_policy_calculations(self):
        before = {path.name:path.read_bytes() for path in self.root.iterdir()}
        result = self.assess(receipt_shocks_cents=[-1000,0,1000])
        self.assertEqual(result["state"],"pass")
        self.assertEqual(result["cash_margin_preview_cents"],8000)
        self.assertEqual(result["contribution_cents"],2000)
        self.assertEqual(result["maximum_acquisition_cents"],20000)
        self.assertEqual([item["contribution_cents"] for item in result["sensitivity"]],[1000,2000,3000])
        self.assertFalse(result["purchase_authorized"])
        self.assertEqual({path.name:path.read_bytes() for path in self.root.iterdir()},before)

    def test_required_unknown_cost_and_unverified_zero_cannot_resolve_contribution(self):
        self.review["cost_lines"][1]["amount_cents"] = None
        self.review["scenario_status"] = "unresolved"
        result = self.assess()
        self.assertEqual(result["cash_margin_preview_cents"],8000)
        self.assertIsNone(result["contribution_cents"])
        self.review["cost_lines"][1].update(amount_cents=0,basis_claim_ids=[])
        self.review["scenario_status"] = "complete"
        result = self.assess()
        self.assertIn("cost_basis_unverified:labor",result["reason_codes"])
        self.assertIsNone(result["maximum_acquisition_cents"])

    def test_reserve_never_reduces_contribution_and_paid_research_cannot_overlap(self):
        self.review["cost_lines"].append(self.line("liquidity",4000,"reserve"))
        self.policy["cost_coverage"].append("liquidity")
        result = self.assess()
        self.assertEqual(result["contribution_cents"],2000)
        self.assertEqual(result["reserve_cents"],4000)
        self.review["cost_lines"][1]["underlying_exposure_ids"] = ["transport"]
        with self.assertRaises(IntegrityError):
            self.assess()

    def test_net_settled_fee_is_counted_once_and_policy_expiry_blocks_current_scenario(self):
        fee = self.line("fees",1000,"cash")
        fee["cost_policy_id"] = "fee-policy"
        self.review["cost_lines"].append(fee)
        self.policy["cost_coverage"].append("fees")
        self.cost_policies["fee-policy"] = dict(deepcopy(RECORDS["cost_policy"]),record_id="fee-policy",account_scope=self.review["scope"],category="fees",valid_until="2026-02-01T12:00:00Z",amount_cents=1000,basis_claim_ids=["fee-policy-basis"])
        gross = self.assess()
        self.review.update(receipts_basis="net",included_cost_exposure_ids=["fees"],item_receipt_cents=32000)
        net = self.assess()
        self.assertEqual(gross["contribution_cents"],net["contribution_cents"])
        self.assertEqual(net["contribution_cents"],1000)
        self.cost_policies["fee-policy"]["valid_until"] = "2026-01-15T12:00:00Z"
        self.assertIsNone(self.assess()["contribution_cents"])
        self.cost_policies["fee-policy"].update(valid_until="2026-02-01T12:00:00Z",rule="unsupported")
        self.assertIsNone(self.assess()["maximum_acquisition_cents"])

    def test_upstream_hashes_legacy_and_changed_retained_bytes_are_rejected(self):
        self.assertEqual(read_result(self.upstream)["outcome"],"supported")
        with self.assertRaises(IntegrityError):
            recheck(dict(self.upstream,result_sha256="0"*64))
        prior = self.review["review_sha256"]
        self.review["review_sha256"] = "0"*64
        with self.assertRaises(IntegrityError):
            self.assess()
        self.review["review_sha256"] = prior
        (self.root/"synthetic-evidence.txt").write_text("changed retained evidence",encoding="utf-8")
        with self.assertRaises(IntegrityError):
            self.assess()
        old = json.loads((self.root/"review.json").read_text(encoding="utf-8"))
        old["schema_version"] = 1
        (self.root/"review.json").write_text(json.dumps(old),encoding="utf-8")
        with self.assertRaises(ContractError):
            evaluate_bound(self.root,self.root/"review.json",record_id="legacy",case_id="case",producer_ref="synthetic-actor",as_of=self.as_of,synthetic=True)

    def test_unreviewed_policy_and_realized_estimates_cannot_pass_as_current_scenarios(self):
        self.policy["basis_claim_ids"] = []
        result = self.assess()
        self.assertIn("operating_policy_not_substantively_verified",result["reason_codes"])
        self.assertIsNone(result["contribution_cents"])
        self.policy["basis_claim_ids"] = ["operating-policy-basis"]
        self.review["scenario_kind"] = "realized"
        result = self.assess()
        self.assertIn("realized_requires_dedicated_cost_review",result["reason_codes"])
        self.assertIsNone(result["maximum_acquisition_cents"])

    def test_transient_review_replacement_cannot_bind_altered_arithmetic(self):
        original = (self.root/"review.json").read_bytes()
        def altered(capture,review_path):
            changed = json.loads(original)
            changed["costs"]["fees"]["eur"] = "99"
            try:
                Path(review_path).write_text(json.dumps(changed),encoding="utf-8")
                return evaluate_capture(capture,review_path)
            finally:
                Path(review_path).write_bytes(original)
        with patch("operations.upstream.evaluate_capture",side_effect=altered),self.assertRaises(IntegrityError):
            evaluate_bound(self.root,self.root/"review.json",record_id="transient",case_id="case",producer_ref="synthetic-actor",as_of=self.as_of,synthetic=True)
        self.assertEqual((self.root/"review.json").read_bytes(),original)

    def test_progressing_clock_allows_later_current_view_but_not_backdating_or_staleness(self):
        with patch("operations.upstream.utc_now",side_effect=["2026-01-15T12:30:00.050000Z","2026-01-15T12:30:01.050000Z"]):
            self.upstream = evaluate_bound(self.root,self.root/"review.json",record_id="upstream",case_id="case",producer_ref="synthetic-actor",as_of=self.as_of,synthetic=True)
            self.review.update(as_of="2026-01-15T12:30:01Z",created_at="2026-01-15T12:30:01Z")
            self.assertEqual(self.assess()["state"],"pass")
        self.review.update(as_of="2026-01-15T12:30:00Z",created_at="2026-01-15T12:30:00Z")
        self.assertIn("upstream_not_available_as_of",self.assess()["reason_codes"])
        self.review.update(as_of="2026-01-15T14:00:00Z",created_at="2026-01-15T14:00:00Z")
        self.assertIn("upstream_snapshot_expired",self.assess()["reason_codes"])

    def test_transient_hash_repair_does_not_make_original_invalid_review_valid(self):
        valid = (self.root/"review.json").read_bytes()
        invalid = json.loads(valid)
        invalid["source_record_sha256"] = "0"*64
        (self.root/"review.json").write_text(json.dumps(invalid),encoding="utf-8")
        invalid_raw = (self.root/"review.json").read_bytes()
        def repair(capture,review_path):
            try:
                Path(review_path).write_bytes(valid)
                return evaluate_capture(capture,review_path)
            finally:
                Path(review_path).write_bytes(invalid_raw)
        with patch("operations.upstream.evaluate_capture",side_effect=repair),self.assertRaises(IntegrityError):
            evaluate_bound(self.root,self.root/"review.json",record_id="invalid",case_id="case",producer_ref="synthetic-actor",as_of=self.as_of,synthetic=True)


if __name__ == "__main__":
    unittest.main()
