"""Fictional desktops and inspectors; no device operations or actual credentials."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from operations.contracts import record_digest
from operations.errors import IntegrityError
from operations.inspection import DESKTOP_CHECKS, PROHIBITED_ACTIONS, assess_inspection

RECORDS = json.loads((Path(__file__).parent/"fixtures/operations-records.json").read_text(encoding="utf-8"))


class DesktopInspectionTests(unittest.TestCase):
    def setUp(self):
        self.protocol = dict(deepcopy(RECORDS["inspection_protocol"]),record_id="desktop-protocol",family="business-desktop",checks=[{"check_id":identity,"method":"Fictional nondestructive observation","evidence_required":"retained fictional notes","pass_rule":"reviewed coverage","unknown_rule":"abstain"} for identity in sorted(DESKTOP_CHECKS)],capability_claim_ids=["capability"],authority_ids=["inspector"],prohibited_operations=sorted(PROHIBITED_ACTIONS))
        configuration = {"cpu":"Fictional CPU A","ram":"Fictional 2x8 configuration","storage":"Fictional drive A","revision":"Fictional chassis v1","accessories":["Fictional power cable"]}
        self.case = dict(deepcopy(RECORDS["inspection_case"]),record_id="inspection",case_id="case",scope="desktop-review",protocol_id="desktop-protocol",inspector_ref="inspector",expected_configuration=configuration,observed_configuration=deepcopy(configuration),tool_inventory={name:"Fictional available "+name for name in RECORDS["inspection_case"]["tool_inventory"]},access_authorized=True,management_release=True,activation_release=True,basis_claim_ids=["case-basis"],check_results=[{"check_id":identity,"status":"pass","origin":"observed","basis_claim_ids":["case-basis"],"notes":"Invented completed coverage"} for identity in sorted(DESKTOP_CHECKS)])
        self.actor = dict(deepcopy(RECORDS["actor_authority"]),record_id="inspector",scopes=["inspect:desktop-review"],grant_claim_ids=["grant"])
        self.records = {"inspector":self.actor}
        self.bind()

    def bind(self):
        for target,identity in ((self.protocol,"capability"),(self.case,"case-basis"),(self.actor,"grant")):
            self.records[identity] = dict(deepcopy(RECORDS["material_claim"]),record_id=identity,subject={"record_id":target["record_id"],"record_type":target["record_type"],"record_sha256":record_digest(target),"verified_fields":list(target)})

    def verify(self,identity,**kwargs):
        claim = self.records[identity]
        return {"state":"pass","claim_id":identity,"claim_sha256":record_digest(claim),"subject":claim["subject"]}

    def assess(self):
        return assess_inspection(self.case,self.protocol,as_of="2026-01-01T12:30:00Z",resolve=self.records.get,verify_claim=self.verify)

    def test_complete_reviewed_fictional_protocol_only_concludes_current_function(self):
        result = self.assess()
        self.assertEqual(result["state"],"pass",result["reason_codes"])
        self.assertTrue(result["working_item_conclusion"])
        self.assertFalse(result["action_performed"])
        self.assertFalse(result["purchase_authorized"])

    def test_wrong_variant_missing_tool_and_unknown_lock_block_working_conclusion(self):
        original = deepcopy(self.case)
        for mutate in (lambda:self.case["observed_configuration"].update(cpu="Different fictional CPU"),lambda:self.case["tool_inventory"].update(storage_health_diagnostic=None),lambda:self.case.update(management_release=None)):
            self.case = deepcopy(original)
            mutate()
            self.bind()
            self.assertFalse(self.assess()["working_item_conclusion"])

    def test_power_on_alone_and_unobserved_seller_diagnosis_are_insufficient(self):
        full = deepcopy(self.case["check_results"])
        self.case["check_results"] = [item for item in full if item["check_id"] == "power_on"]
        self.bind()
        self.assertEqual(self.assess()["state"],"unknown")
        self.case["check_results"] = [dict(item,origin="seller_diagnosis") for item in full]
        self.bind()
        self.assertFalse(self.assess()["working_item_conclusion"])

    def test_repair_forecast_and_parts_out_never_replace_current_condition(self):
        for scenario in ("if_repaired","parts_out"):
            self.case["scenario"] = scenario
            self.bind()
            result = self.assess()
            self.assertEqual(result["scenario"],scenario)
            self.assertFalse(result["working_item_conclusion"])

    def test_interventions_never_run_even_with_a_reviewed_case(self):
        for action in PROHIBITED_ACTIONS:
            self.case["requested_action"] = action
            self.bind()
            result = self.assess()
            self.assertEqual(result["state"],"fail")
            self.assertFalse(result["action_performed"])
        self.case["requested_action"] = "inspect"
        self.actor["scopes"] = []
        self.bind()
        self.assertFalse(self.assess()["working_item_conclusion"])

    def test_changed_bytes_and_duplicate_observations_do_not_reuse_old_review(self):
        self.case["observed_configuration"]["revision"] = "Different fictional revision"
        self.assertFalse(self.assess()["working_item_conclusion"])
        self.case["check_results"].append(dict(self.case["check_results"][0],notes="conflicting duplicate"))
        with self.assertRaises(IntegrityError):
            self.assess()

    def test_additional_known_fault_is_not_discarded_by_protocol_filter(self):
        self.case["check_results"].append({"check_id":"intermittent_shutdown","status":"fail","origin":"observed","basis_claim_ids":["case-basis"],"notes":"Invented additional fault"})
        self.bind()
        result = self.assess()
        self.assertEqual(result["state"],"fail")
        self.assertFalse(result["working_item_conclusion"])
        self.assertIn("function_check_failed:intermittent_shutdown",result["reason_codes"])


if __name__ == "__main__":
    unittest.main()
