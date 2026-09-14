"""Integrated synthetic readiness using the real retained-evidence evaluator."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from operations import capacity, service, store
from operations.contracts import record_digest
from operations.errors import IntegrityError, UnresolvedError
from operations.sources import SCOPE_FIELDS
from operations.upstream import evaluate_bound
from resale_tool.demo import NOTICE, sample, write_json
from test_operations_capacity import RECORDS, ResourceFixture, verifier


class ReadinessFixture:
    def __init__(self,root,*,route_changes=None):
        self.resources = ResourceFixture(root)
        self.path = self.resources.path
        self.claims = self.resources.claims
        self.now = "2026-01-15T12:30:00Z"
        self.resources.now = self.now
        self.capture = root/"capture"
        self.capture.mkdir()
        record,review = sample("case",200)
        for index,comp in enumerate(review["comps"]):
            comp["item_price_eur"] = str(330+index*10)
        write_json(self.capture/"record.json",record)
        review["source_record_sha256"] = hashlib.sha256((self.capture/"record.json").read_bytes()).hexdigest()
        write_json(self.capture/"review.json",review)
        (self.capture/"synthetic-evidence.txt").write_text(NOTICE,encoding="utf-8")
        with patch("operations.upstream.utc_now",return_value=self.now):
            self.upstream = evaluate_bound(self.capture,self.capture/"review.json",record_id="upstream",case_id="case",producer_ref="actor",as_of=self.now,synthetic=True)
        self.put(self.upstream)
        self.actor = self.make("actor_authority","actor",identity_ref="synthetic-host-user",scopes=["human_decision:synthetic-account","manual_preflight:synthetic-account","portfolio_control:synthetic-account"],grant_claim_ids=["grant"])
        self.put(self.actor)
        self.claim(self.actor,"grant")
        self.scenario = self.make("operating_review","scenario",case_id="case",scope="synthetic-account",created_at=self.now,as_of=self.now,upstream_evaluation_id="upstream",upstream_result_sha256=self.upstream["result_sha256"],capture_sha256=self.upstream["capture_sha256"],review_sha256=self.upstream["review_sha256"],policy_id="policy",item_receipt_cents=33000,shipping_collected_cents=0,acquisition_cents=20000,scenario_status="complete",basis_claim_ids=["scenario-basis"],assumptions=["Invented fixed forecast"],cost_lines=[{"exposure_id":name,"category":name,"amount_cents":amount,"treatment":treatment,"basis_kind":"synthetic","basis_claim_ids":["scenario-basis"],"incurred_at":None,"effective_until":None,"allocation_rule_id":"case","cost_policy_id":None,"underlying_exposure_ids":[name]} for name,amount,treatment in (("transport",5000,"cash"),("labor",6000,"noncash"))])
        self.put(self.scenario)
        self.claim(self.scenario,"scenario-basis")
        self.route = self.make("transaction_route","route",case_id="case",item_id="fictional-device",buyer_identity_ref="buyer",seller_identity_ref="seller",payment_destination_sha256="a"*64,acquisition_cents=20000,intervention_plan_sha256="b"*64,protection_required=True,protection_status="available",eligibility="eligible",item_party_payment_binding="established",binding_claim_ids=["binding"],ownership_claim_ids=["ownership"],eligibility_claim_ids=["eligibility"],terms_ref="terms",claim_deadline_review_ref="deadline-policy",inspection_before_release_plan_ref="inspection",deadline_ids=["deadline"])
        self.route.update(route_changes or {})
        self.put(self.route)
        for identity,kind in (("binding","identity"),("ownership","ownership"),("eligibility","legal_applicability"),("terms","legal_applicability"),("deadline-policy","legal_applicability")):
            self.claim(self.route,identity,kind)
        inspection = self.make("inspection_protocol","inspection",checks=[{"check_id":"visual","method":"synthetic","evidence_required":"notes","pass_rule":"reviewed","unknown_rule":"abstain"}],authority_ids=["actor"],capability_claim_ids=["capability"])
        self.put(inspection)
        self.claim(inspection,"capability","function")
        deadline = self.make("reviewed_deadline","deadline",case_id="case",route_id="route",trigger_claim_id="trigger",effective_deadline_at="2026-01-16T12:00:00Z",basis_claim_ids=["deadline-basis"])
        self.put(deadline)
        self.claim(deadline,"deadline-basis","legal_applicability")
        self.claim(deadline,"trigger")
        source = self.make("source_rule","source",collection="allowed",retention="allowed",basis_refs=["source-basis"])
        self.put(source)
        self.claim(source,"source-basis","legal_applicability")
        for index in (1,2):
            event = self.make("evidence_event","demo-sale-"+str(index),event_id="sale-"+str(index),transaction_state="sold_reported",source_rule_id="source",price_basis="actual_sold_item_price",item_price_cents=32000+index*1000,units=1,seller_country="DE",lineage_status="established",event_at="2026-01-01T11:00:00Z",material_claim_ids=["price-"+str(index)])
            self.put(event)
            self.claim(event,"price-"+str(index))
        snapshot = dict(self.resources.snapshot,record_id="current-capacity",revision=2,created_at=self.now,as_of=self.now,valid_until="2026-01-16T12:30:00Z",cleared_cash_cents=50000,work_minutes=180,work_period="2026-01-15",expected_resource_revision=1,evidence_claim_ids=["current-capacity-basis"])
        self.resources.statement(snapshot)
        hold = self.resources.reservation()
        hold.update(case_id="case",expires_at="2026-01-15T13:00:00Z",resource_requests={"cash_cents":25000,"work_minutes":180,"storage_units":1})
        self.resources.reserve(hold)
        self.request = self.make("readiness_request","request",case_id="case",scope="synthetic-account",actor_ref="actor",policy_id="policy",upstream_evaluation_id="upstream",operating_review_id="scenario",route_id="route",reservation_id="hold",source_requests=[{"rule_id":"source","action":"retention","context":{key:source[key] for key in SCOPE_FIELDS}}],evidence_event_ids=["demo-sale-1","demo-sale-2"],lineage_edge_ids=[],basis_claim_ids=["request-basis"],resource_requirements=hold["resource_requests"])
        self.put(self.request)
        self.claim(self.request,"request-basis")

    def make(self,kind,identity,**updates):
        record = dict(deepcopy(RECORDS[kind]),record_id=identity)
        if "valid_until" in record:
            record["valid_until"] = "2026-01-16T12:30:00Z"
        record.update(updates)
        return record

    def put(self,record):
        return store.append(self.path,record,request_key=record["record_id"])

    def claim(self,target,identity,kind="identity"):
        claim = self.make("material_claim",identity,claim_type=kind,subject={"record_id":target["record_id"],"record_type":target["record_type"],"record_sha256":record_digest(target),"verified_fields":list(target)})
        self.claims[identity] = claim
        self.put(claim)

    def assess(self,verify=None):
        with patch("operations.service.utc_now",return_value=self.now),patch("operations.upstream.utc_now",return_value=self.now):
            return service.assess(self.path,self.request,verify_claim=verify or verifier(self.claims))

    def prepare(self,identity="ready"):
        with patch("operations.service.utc_now",return_value=self.now),patch("operations.upstream.utc_now",return_value=self.now):
            return service.prepare(self.path,self.request,record_id=identity,request_key=identity,verify_claim=verifier(self.claims))


class ReadinessServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = ReadinessFixture(Path(self.temp.name))

    def test_real_module_composition_has_stable_material_binding_as_clock_advances(self):
        fixture = self.fixture
        first = fixture.assess()
        self.assertEqual(first["status"],"ready_for_human_review",first["checks"])
        self.assertIs(first["purchase_authorized"],False)
        fixture.now = "2026-01-15T12:31:00Z"
        second = fixture.assess()
        self.assertEqual(first["binding_sha256"],second["binding_sha256"])
        before = fixture.path.read_bytes()
        fixture.assess()
        self.assertEqual(fixture.path.read_bytes(),before)
        fixture.prepare()
        snapshot = store.get_record(fixture.path,"ready")
        self.assertEqual(snapshot["binding_sha256"],first["binding_sha256"])
        self.assertFalse(snapshot["purchase_authorized"])
        with self.assertRaises(IntegrityError):
            store.append(fixture.path,dict(snapshot,record_id="forged"),request_key="forged")

    def test_new_refund_cannot_be_hidden_by_frozen_request_event_list(self):
        fixture = self.fixture
        sale = store.get_record(fixture.path,"demo-sale-1")
        fixture.put(dict(sale,record_id="refund",transaction_state="refunded",related_event_ids=["demo-sale-1"],created_at=fixture.now,observed_at=fixture.now,event_at=fixture.now))
        self.assertNotEqual(fixture.assess()["checks"]["integrity"]["state"],"pass")

    def test_missing_proof_and_changed_original_bytes_never_remain_ready(self):
        fixture = self.fixture
        original = verifier(fixture.claims)
        def missing(identity,**kwargs):
            result = original(identity,**kwargs)
            if identity == "request-basis":
                result["state"] = "unknown"
            return result
        self.assertEqual(fixture.assess(missing)["status"],"unresolved")
        (fixture.capture/"review.json").write_text("{}",encoding="utf-8")
        self.assertEqual(fixture.assess()["status"],"blocked")

    def test_new_route_or_policy_revision_requires_new_request(self):
        fixture = self.fixture
        fixture.put(dict(fixture.route,record_id="changed-route",acquisition_cents=21000,created_at=fixture.now))
        with self.assertRaises(UnresolvedError):
            fixture.assess()

    def test_later_source_denial_and_actor_revocation_invalidate_old_readiness(self):
        fixture = self.fixture
        source = store.get_record(fixture.path,"source")
        fixture.put(dict(source,record_id="source-denied",created_at=fixture.now,checked_at=fixture.now,retention="denied",basis_refs=["denial-basis"]))
        self.assertNotEqual(fixture.assess()["checks"]["rights"]["state"],"pass")
        fixture.put(dict(fixture.actor,record_id="revoked",created_at=fixture.now,revoked_at=fixture.now,revocation_claim_ids=["revocation-basis"]))
        self.assertNotEqual(fixture.assess()["checks"]["integrity"]["state"],"pass")

    def test_readiness_expiry_includes_nested_reviewed_deadline(self):
        fixture = self.fixture
        deadline = store.get_record(fixture.path,"deadline")
        updated = dict(deadline,record_id="early-deadline",effective_deadline_at="2026-01-15T12:31:00Z",basis_claim_ids=["early-basis"])
        fixture.put(updated)
        fixture.claim(updated,"early-basis","legal_applicability")
        route = dict(fixture.route,record_id="early-route",deadline_ids=["early-deadline"],binding_claim_ids=["early-route-basis"],ownership_claim_ids=["early-ownership"],eligibility_claim_ids=["early-eligibility"],terms_ref="early-terms",claim_deadline_review_ref="early-policy")
        # Build a complete separately bound route/deadline pair, not edited history.
        updated["route_id"] = "early-route"
        updated["record_id"] = "bound-early-deadline"
        updated["basis_claim_ids"] = ["bound-early-basis"]
        route["deadline_ids"] = [updated["record_id"]]
        fixture.put(updated)
        fixture.claim(updated,"bound-early-basis","legal_applicability")
        fixture.put(route)
        for identity,kind in (("early-route-basis","identity"),("early-ownership","ownership"),("early-eligibility","legal_applicability"),("early-terms","legal_applicability"),("early-policy","legal_applicability")):
            fixture.claim(route,identity,kind)
        fixture.request = dict(fixture.request,record_id="early-request",route_id="early-route",basis_claim_ids=["early-request-basis"])
        fixture.claim(fixture.request,"early-request-basis")
        result = fixture.assess()
        self.assertEqual(result["status"],"ready_for_human_review",result["checks"])
        self.assertEqual(result["valid_until"],"2026-01-15T12:31:00Z")


if __name__ == "__main__":
    unittest.main()
