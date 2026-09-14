"""Fictional route applicability; no provider/legal defaults or payment calls."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from operations.errors import ContractError

from operations.contracts import record_digest
from operations.routes import assess_route, ROUTE_FIELDS

RECORDS = json.loads((Path(__file__).parent/"fixtures/operations-records.json").read_text(encoding="utf-8"))


class TransactionRouteTests(unittest.TestCase):
    def setUp(self):
        self.route = dict(deepcopy(RECORDS["transaction_route"]),record_id="route",case_id="case",item_id="fictional-device",buyer_identity_ref="fictional-buyer",seller_identity_ref="fictional-seller",payment_destination_sha256="a"*64,acquisition_cents=20000,intervention_plan_sha256="b"*64,protection_required=True,protection_status="available",eligibility="eligible",item_party_payment_binding="established",binding_claim_ids=["binding"],ownership_claim_ids=["ownership"],eligibility_claim_ids=["eligibility"],terms_ref="terms",claim_deadline_review_ref="deadline-policy",inspection_before_release_plan_ref="inspection",deadline_ids=["deadline"])
        self.inspection = dict(deepcopy(RECORDS["inspection_protocol"]),record_id="inspection",checks=[{"check_id":"visual","method":"synthetic checklist","evidence_required":"retained notes","pass_rule":"reviewed","unknown_rule":"abstain"}],authority_ids=["synthetic-authority"],capability_claim_ids=["capability"])
        self.deadline = dict(deepcopy(RECORDS["reviewed_deadline"]),record_id="deadline",case_id="case",route_id="route",trigger_claim_id="trigger",effective_deadline_at="2026-01-02T10:00:00Z",basis_claim_ids=["deadline-basis"])
        self.records = {"inspection":self.inspection,"deadline":self.deadline}
        for identity,kind in (("binding","identity"),("ownership","ownership"),("eligibility","legal_applicability"),("terms","legal_applicability"),("deadline-policy","legal_applicability")):
            self.add_claim(identity,kind,self.route,list(ROUTE_FIELDS)+["eligibility"])
        self.add_claim("capability","function",self.inspection,list(self.inspection))
        self.add_claim("deadline-basis","legal_applicability",self.deadline,list(self.deadline))
        self.add_claim("trigger","identity",self.deadline,list(self.deadline))
        self.as_of = "2026-01-01T12:30:00Z"

    def add_claim(self,identity,kind,target,fields):
        claim = dict(deepcopy(RECORDS["material_claim"]),record_id=identity,claim_type=kind,subject={"record_id":target["record_id"],"record_type":target["record_type"],"record_sha256":record_digest(target),"verified_fields":fields})
        self.records[identity] = claim

    def verify(self,identity,**kwargs):
        claim = self.records[identity]
        return {"state":"pass","claim_id":identity,"claim_sha256":record_digest(claim),"subject":claim["subject"],"limitations":[],"review_limitations":[]}

    def assess(self):
        return assess_route(self.route,case_id="case",as_of=self.as_of,synthetic=True,resolve=self.records.get,verify_claim=self.verify)

    def test_fully_reviewed_fictional_route_passes_without_purchase_authority(self):
        result = self.assess()
        self.assertEqual(result["state"],"pass",result["reason_codes"])
        self.assertFalse(result["purchase_authorized"])

    def test_required_unavailable_protection_cannot_be_overridden_by_provider_brand(self):
        self.route.update(provider="PayPal (fictional applicability fixture)",protection_status="unavailable")
        result = self.assess()
        self.assertEqual(result["state"],"fail")
        self.assertIn("required_protection_unavailable",result["reason_codes"])

    def test_material_party_price_terms_or_plan_change_invalidates_prior_binding(self):
        for field,value in (("seller_identity_ref","different"),("acquisition_cents",20100),("delivery_method","different"),("terms_version","different"),("intervention_plan_sha256","c"*64)):
            old = self.route[field]
            self.route[field] = value
            self.assertNotEqual(self.assess()["state"],"pass",field)
            self.route[field] = old
        self.route["item_party_payment_binding"] = "contradicted"
        self.assertEqual(self.assess()["state"],"fail")
        self.route["item_party_payment_binding"] = "established"
        self.assertEqual(self.assess()["state"],"pass")

    def test_missing_ownership_deadline_or_expiry_never_defaults_eligible(self):
        ownership = self.records.pop("ownership")
        self.assertNotEqual(self.assess()["state"],"pass")
        self.records["ownership"] = ownership
        deadline = self.records.pop("deadline")
        self.assertIn("reviewed_deadline_missing",self.assess()["reason_codes"])
        self.records["deadline"] = deadline
        self.as_of = "2026-01-02T12:00:00Z"
        self.assertNotEqual(self.assess()["state"],"pass")

    def test_checked_claim_digest_is_not_replaced_by_a_second_resolver_read(self):
        counts = {}
        def changing(identity):
            counts[identity] = counts.get(identity,0)+1
            record = self.records.get(identity)
            if identity == "binding" and counts[identity] > 1:
                return dict(record,statement="changed after verification")
            return record
        result = assess_route(self.route,case_id="case",as_of=self.as_of,synthetic=True,resolve=changing,verify_claim=self.verify)
        self.assertEqual(result["state"],"pass")
        self.assertEqual(counts["binding"],1)
        self.assertEqual(result["input_digests"]["binding"],result["checks"]["bindings"]["input_digests"]["binding"])

    def test_trigger_review_provenance_changes_assessment_and_bad_callbacks_are_typed(self):
        def versioned(version):
            def verify(identity,**kwargs):
                result = self.verify(identity,**kwargs)
                if identity == "trigger":
                    result["review_ids"] = [version]
                return result
            return verify
        inputs = dict(case_id="case",as_of=self.as_of,synthetic=True,resolve=self.records.get)
        first = assess_route(self.route,verify_claim=versioned("review-a"),**inputs)
        second = assess_route(self.route,verify_claim=versioned("review-b"),**inputs)
        self.assertNotEqual(first,second)
        for value in (None,[],True,{"state":[]}):
            with self.assertRaises(ContractError):
                assess_route(self.route,verify_claim=lambda *args,**kwargs:value,**inputs)


if __name__ == "__main__":
    unittest.main()
