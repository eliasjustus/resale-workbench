"""Invented service differences and fixed scenarios; no sampled confidence claims."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from operations.comparability import assess_comparisons
from operations.contracts import record_digest
from operations.errors import IntegrityError
from operations.serialization import digest
from operations.sensitivity import compare_sensitivity

RECORDS = json.loads((Path(__file__).parent/"fixtures/operations-records.json").read_text(encoding="utf-8"))


class ComparabilityTests(unittest.TestCase):
    def setUp(self):
        self.events = [dict(deepcopy(RECORDS["evidence_event"]),record_id=identity,event_id="sale-"+identity,transaction_state="sold_reported",price_basis="actual_sold_item_price",item_price_cents=price,units=1,seller_country="DE",lineage_status="established",event_at="2026-01-01T11:00:00Z",material_claim_ids=[identity+"-basis"]) for identity,price in (("a",10000),("b",14000))]
        self.assessment = dict(deepcopy(RECORDS["comparison_assessment"]),record_id="comparison",case_id="case",scope="account",target_offer={"variant":"Fictional exact variant","condition":"current_condition","seller_role":"private","channel":"fictional channel","return_terms_ref":"reviewed-target-terms","testing_scope_refs":["target-testing"],"included_accessories":["fictional cable"],"reputation_context_ref":"reviewed-no-premium"},comparisons=[{"evidence_event_id":event["record_id"],"disposition":"included","observed_variant":"Fictional exact variant","transfer_argument":"Fictional reviewed explanation of service differences; no automatic premium","adjustment_policy_id":None,"basis_claim_ids":["target-basis"]} for event in self.events],basis_claim_ids=["target-basis"])
        self.records = {}
        for bundle in [self.assessment["target_offer"]]+[event["service_bundle"] for event in self.events]:
            for identity in [bundle["return_terms_ref"],*bundle["testing_scope_refs"],bundle["reputation_context_ref"]]:
                self.records[identity] = dict(deepcopy(RECORDS["material_claim"]),record_id=identity,status="established",subject=None,source_refs=[{"relative_path":"synthetic-service.txt","sha256":"a"*64,"locator":"fictional service paragraph"}])
        self.bind()
        self.as_of = "2026-01-01T12:30:00Z"

    def claim(self,target,identity):
        self.records[identity] = dict(deepcopy(RECORDS["material_claim"]),record_id=identity,subject={"record_id":target["record_id"],"record_type":target["record_type"],"record_sha256":record_digest(target),"verified_fields":list(target)})

    def bind(self):
        self.claim(self.assessment,"target-basis")
        for event in self.events:
            self.claim(event,event["material_claim_ids"][0])

    def verify(self,identity,**kwargs):
        claim = self.records[identity]
        return {"state":"pass","claim_id":identity,"claim_sha256":record_digest(claim),"subject":claim["subject"]}

    def assess(self):
        return assess_comparisons(self.assessment,self.events,as_of=self.as_of,resolve=self.records.get,verify_claim=self.verify)

    def sensitivity(self,*policies):
        return compare_sensitivity(self.assessment,self.events,as_of=self.as_of,resolve=self.records.get,verify_claim=self.verify,assumption_policy_ids=policies)

    def policy(self,identity,amount,event=None,**updates):
        record = dict(deepcopy(RECORDS["comparison_adjustment"]),record_id=identity,case_id="case",scope="account",scenario=self.assessment["scenario"],offer_sha256=digest({"scenario":self.assessment["scenario"],"offer":self.assessment["target_offer"]}),evidence_event_id=event,amount_cents=amount,basis_claim_ids=[identity+"-basis"])
        record.update(updates)
        self.records[identity] = record
        self.claim(record,identity+"-basis")
        return record

    def test_unknown_target_or_unverified_source_service_is_not_inherited(self):
        self.assertEqual(self.assess()["state"],"pass")
        self.assessment["target_offer"]["return_terms_ref"] = None
        self.bind()
        result = self.assess()
        self.assertIsNone(result["target_offer"]["return_terms_ref"])
        self.assertIsNone(result["conditional_floor_cents"])
        self.assertEqual(result["comparisons"][0]["source_service_bundle"],self.events[0]["service_bundle"])
        self.assessment["target_offer"]["return_terms_ref"] = "reviewed-target-terms"
        self.bind()
        self.records["a-basis"]["subject"]["verified_fields"].remove("service_bundle")
        self.assertIn("source_price_or_service_bundle_unverified:a",self.assess()["reason_codes"])

    def test_duplicate_event_cannot_improve_floor_count_or_omission_result(self):
        before = self.sensitivity()
        copy = dict(self.events[0],record_id="copy",material_claim_ids=["copy-basis"])
        self.events.append(copy)
        self.assessment["comparisons"].append(dict(self.assessment["comparisons"][0],evidence_event_id="copy"))
        self.bind()
        result = self.sensitivity()
        self.assertEqual(result["baseline"]["independent_event_count"],2)
        self.assertEqual(result["baseline"]["conditional_floor_cents"],before["baseline"]["conditional_floor_cents"])
        omitted = next(item for item in result["leave_one_material_event_out"] if "a" in item["omitted_evidence_event_ids"])
        self.assertEqual(omitted["omitted_evidence_event_ids"],["a","copy"])
        self.assertEqual(omitted["conditional_floor_cents"],14000)

    def test_changed_service_or_condition_is_a_new_binding_not_an_edited_baseline(self):
        original = deepcopy(self.assessment)
        first = self.assess()
        self.assessment = dict(deepcopy(self.assessment),record_id="changed-comparison")
        self.assessment["target_offer"]["condition"] = "if_repaired"
        self.bind()
        second = self.assess()
        self.assertNotEqual(first["comparison_sha256"],second["comparison_sha256"])
        self.assertIsNone(second["conditional_floor_cents"])
        self.assertEqual(original["target_offer"]["condition"],"current_condition")
        self.assertEqual(first["conditional_floor_cents"],10000)

    def test_only_reviewed_fixed_cents_can_adjust_events_or_assumption_ranges(self):
        self.assessment["comparisons"][0]["adjustment_policy_id"] = "missing"
        self.bind()
        self.assertIsNone(self.assess()["conditional_floor_cents"])
        self.policy("discount",-1000,event="a")
        self.assessment["comparisons"][0]["adjustment_policy_id"] = "discount"
        self.bind()
        self.assertEqual(self.assess()["conditional_floor_cents"],9000)
        self.policy("low",-2000)
        self.policy("high",1000)
        result = self.sensitivity("low","high")
        self.assertEqual(result["conditional_scenario_range_cents"],[7000,10000])
        self.assertIn("not_confidence_interval",result["range_kind"])
        self.policy("unsupported",20,rule="unsupported")
        self.assertIsNone(self.sensitivity("low","unsupported")["conditional_scenario_range_cents"])

    def test_every_event_has_disposition_and_same_event_price_conflicts_abstain(self):
        entry = self.assessment["comparisons"].pop()
        with self.assertRaises(IntegrityError):
            self.assess()
        self.assessment["comparisons"].append(entry)
        self.events[1]["event_id"] = self.events[0]["event_id"]
        self.bind()
        result = self.assess()
        self.assertIsNone(result["conditional_floor_cents"])
        self.assertTrue(any(code.startswith("same_event_price_or_adjustment_conflict") for code in result["reason_codes"]))

    def test_disputed_and_excluded_records_remain_visible(self):
        self.assessment["comparisons"][0]["disposition"] = "excluded"
        self.assessment["comparisons"][1]["disposition"] = "disputed"
        self.bind()
        result = self.assess()
        self.assertEqual([item["disposition"] for item in result["comparisons"]],["excluded","disputed"])
        self.assertEqual(result["state"],"unknown")

    def test_service_references_must_resolve_and_their_changes_invalidate_binding(self):
        before = self.assess()
        self.assertEqual(before["state"],"pass")
        identity = "reviewed-target-terms"
        claim = self.records.pop(identity)
        self.assertIn("service_reference_missing:"+identity,self.assess()["reason_codes"])
        self.records[identity] = dict(claim,status="contradicted")
        result = self.assess()
        self.assertEqual(result["state"],"unknown")
        self.assertNotEqual(result["assessment_sha256"],before["assessment_sha256"])
        self.assertIn(identity,result["input_digests"])
        self.records[identity] = claim
        source_id = self.events[0]["service_bundle"]["return_terms_ref"]
        self.records.pop(source_id)
        self.assertIsNone(self.assess()["conditional_floor_cents"])


if __name__ == "__main__":
    unittest.main()
