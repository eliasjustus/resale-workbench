"""Invented owned items, local drafts and observed-record imports; no publication."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from operations import cohorts, offers, store
from operations.contracts import record_digest
from operations.errors import ConflictError, IntegrityError, UnresolvedError
from operations.experiments import documentation
from operations.inspection import DESKTOP_CHECKS, PROHIBITED_ACTIONS
from test_operations_capacity import RECORDS, ResourceFixture, verifier


class DocumentationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = fixture = ResourceFixture(Path(self.temp.name))
        self.now = fixture.now
        self.items = []
        definition = self.make("cohort_definition","cohort",scope="synthetic-account",basis_claim_ids=["cohort-basis"])
        self.put(definition)
        self.bind(definition,"cohort-basis")
        actor = self.make("actor_authority","inspector",scopes=["inspect:synthetic-account"],grant_claim_ids=["grant"])
        self.put(actor)
        self.bind(actor,"grant")
        inspection_protocol = self.make("inspection_protocol","desktop",family="business-desktop",authority_ids=["inspector"],capability_claim_ids=["capability"],prohibited_operations=sorted(PROHIBITED_ACTIONS),checks=[{"check_id":name,"method":"Invented observation","evidence_required":"Invented notes","pass_rule":"reviewed","unknown_rule":"abstain"} for name in sorted(DESKTOP_CHECKS)])
        self.put(inspection_protocol)
        self.bind(inspection_protocol,"capability")
        for index in (1,2):
            identity = "item-"+str(index)
            configuration = {"cpu":"Fictional CPU","ram":"Fictional RAM","storage":"Fictional storage","revision":"Fictional desktop","accessories":["Fictional cable"]}
            inspection = self.make("inspection_case","inspection-"+identity,case_id=identity,scope="synthetic-account",protocol_id="desktop",inspector_ref="inspector",expected_configuration=configuration,observed_configuration=configuration,tool_inventory={key:"Invented tool" for key in RECORDS["inspection_case"]["tool_inventory"]},access_authorized=True,management_release=True,activation_release=True,basis_claim_ids=["inspection-basis-"+identity],check_results=[{"check_id":name,"status":"fail" if name == "display" else "pass","origin":"observed","basis_claim_ids":["inspection-basis-"+identity],"notes":"Invented display defect" if name == "display" else "Invented completed observation"} for name in sorted(DESKTOP_CHECKS)])
            self.put(inspection)
            self.bind(inspection,"inspection-basis-"+identity)
            item = self.make("offer_item",identity,case_id=identity,physical_item_id=identity,scope="synthetic-account",owner_actor_ref="owner",title="Fictional desktop",price_cents=5000,channel="fictional-channel",inspection_case_id=inspection["record_id"],defects=["Invented cosmetic scratch"],mandatory_disclosures=["Invented material disclosure"],services=["Documented inspection only"],basis_claim_ids=["offer-basis-"+identity],ownership_claim_ids=["ownership-"+identity])
            self.put(item)
            self.bind(item,"offer-basis-"+identity)
            self.bind(item,"ownership-"+identity,kind="ownership")
            self.items.append(item)
            inclusion = self.make("cohort_inclusion","include-"+identity,cohort_id="cohort",cohort_sha256=record_digest(definition),case_id=identity,scope="synthetic-account",lead_id="lead-"+identity,position=index,observed_at=self.now,created_at=self.now,basis_claim_ids=["include-basis-"+identity])
            self.bind(inclusion,inclusion["basis_claim_ids"][0])
            with patch("operations.cohorts.utc_now",return_value=self.now):
                cohorts.include(fixture.path,inclusion,request_key=inclusion["record_id"])
        self.protocol = self.make("documentation_protocol","documentation",scope="synthetic-account",cohort_id="cohort",channel="fictional-channel",buyer_segment_hypothesis="Invented buyer hypothesis",basis_claim_ids=["documentation-basis"],items=[{"physical_item_id":item["physical_item_id"],"offer_id":item["record_id"],"offer_sha256":record_digest(item),"block_id":"matched-pair"} for item in self.items])
        self.put(self.protocol)
        self.bind(self.protocol,"documentation-basis")

    def make(self,kind,identity,**updates):
        return dict(deepcopy(RECORDS[kind]),record_id=identity,**updates)

    def put(self,record):
        return store.append(self.fixture.path,record,request_key=record["record_id"])

    def bind(self,record,identity,kind="identity"):
        if kind == "identity":
            return self.fixture.bind(record,identity)
        claim = self.make("material_claim",identity,claim_type=kind,subject={"record_id":record["record_id"],"record_type":record["record_type"],"record_sha256":record_digest(record),"verified_fields":list(record)})
        self.fixture.claims[identity] = claim
        self.put(claim)

    def assign(self,identity):
        with patch("operations.experiments.documentation.utc_now",return_value=self.now):
            return documentation.assign(self.fixture.path,"documentation",identity,record_id="assign-"+identity,request_key="assign-"+identity,verify_claim=verifier(self.fixture.claims))

    def report(self):
        return documentation.report(self.fixture.path,"documentation",as_of=self.now,verify_claim=verifier(self.fixture.claims))

    def test_both_drafts_preserve_defects_disclosures_and_exact_same_facts(self):
        resolve = lambda identity:store.get_record(self.fixture.path,identity)
        before = self.fixture.path.read_bytes()
        drafts = [offers.render(self.items[0],arm=arm,as_of=self.now,resolve=resolve,verify_claim=verifier(self.fixture.claims)) for arm in ("plain","structured")]
        for draft in drafts:
            self.assertIn("Invented display defect",draft)
            self.assertIn("Invented cosmetic scratch",draft)
            self.assertIn("Invented material disclosure",draft)
            self.assertIn("Not established",draft)
            self.assertIn("SYNTHETIC",draft)
        self.assertEqual([line.removeprefix("- ") for line in drafts[1].splitlines() if line],[line for line in drafts[0].splitlines() if line])
        self.assertEqual(self.fixture.path.read_bytes(),before)
        with self.assertRaises(UnresolvedError):
            offers.render(dict(self.items[0],services=["Invented lifetime warranty"]),arm="plain",as_of=self.now,resolve=resolve,verify_claim=verifier(self.fixture.claims))
        with self.assertRaises(UnresolvedError):
            offers.render(dict(self.items[0],ownership_claim_ids=[]),arm="plain",as_of=self.now,resolve=resolve,verify_claim=verifier(self.fixture.claims))

    def test_each_physical_item_gets_one_balanced_assignment_and_no_fake_second_sale(self):
        first = self.assign("item-1")
        self.assertEqual(self.assign("item-1"),first)
        self.assign("item-2")
        assignments = [store.get_record(self.fixture.path,"assign-item-"+str(index)) for index in (1,2)]
        self.assertEqual({record["arm"] for record in assignments},{"plain","structured"})
        with patch("operations.experiments.documentation.utc_now",return_value=self.now):
            with self.assertRaises(ConflictError):
                documentation.assign(self.fixture.path,"documentation","item-1",record_id="duplicate",request_key="duplicate",verify_claim=verifier(self.fixture.claims))
        report = self.report()
        self.assertEqual(sum(arm["assigned_items"] for arm in report["arms"].values()),2)
        self.assertEqual(sum(arm["unknown_observations"] for arm in report["arms"].values()),2)
        self.assertIsNone(report["reconciled_contribution_cents"])
        self.assertFalse(report["published"])

    def test_price_service_change_invalidates_contrast_and_preserves_assignment(self):
        self.assign("item-1")
        revised = dict(self.items[0],record_id="changed-offer",price_cents=6000,services=["Different substantive service"],basis_claim_ids=["changed-basis"],ownership_claim_ids=["changed-ownership"])
        self.put(revised)
        self.bind(revised,"changed-basis")
        self.bind(revised,"changed-ownership",kind="ownership")
        report = self.report()
        case = next(arm["cases"]["item-1"] for arm in report["arms"].values() if "item-1" in arm["cases"])
        self.assertFalse(case["documentation_only_contrast_valid"])
        self.assertEqual(case["change_disclosure_status"],"undocumented_change")
        observation = self.make("documentation_observation","inquiry",created_at=self.now,occurred_at=self.now,assignment_id="assign-item-1",event_id="one-real-observation",event_type="inquiry",count=1,operator_minutes=5,current_offer_id="changed-offer",deviation_reason="Recorded price and service deviation",basis_claim_ids=["inquiry-basis"])
        self.put(observation)
        self.bind(observation,"inquiry-basis")
        report = self.report()
        arm = next(arm for arm in report["arms"].values() if "item-1" in arm["cases"])
        self.assertEqual(arm["known_inquiries"],1)
        self.assertEqual(arm["cases"]["item-1"]["change_disclosure_status"],"recorded_deviation")
        self.assertFalse(arm["cases"]["item-1"]["documentation_only_contrast_valid"])
        self.assertEqual(arm["assigned_items"],1)

    def test_duplicate_observation_id_cannot_double_count_inquiries(self):
        self.assign("item-1")
        for identity in ("copy-a","copy-b"):
            record = self.make("documentation_observation",identity,created_at=self.now,occurred_at=self.now,assignment_id="assign-item-1",event_id="same-inquiry",event_type="inquiry",count=1,operator_minutes=2,current_offer_id="item-1",basis_claim_ids=[identity+"-basis"])
            self.put(record)
            self.bind(record,identity+"-basis")
        report = self.report()
        self.assertEqual(sum(arm["known_inquiries"] for arm in report["arms"].values()),0)
        self.assertEqual(sum(arm["unknown_observations"] for arm in report["arms"].values()),2)

    def test_unqualified_authority_never_advertises_passed_checks(self):
        with store._connection(self.fixture.path) as connection:
            retained = {record["record_id"]:record for _,record in store._events(connection)}
        for change in ({"scopes":[]},{"valid_until":"2026-01-01T12:01:00Z"}):
            with self.subTest(change=change):
                records = deepcopy(retained)
                records["inspector"].update(change)
                records["grant"]["subject"]["record_sha256"] = record_digest(records["inspector"])
                claims = {identity:record for identity,record in records.items() if record["record_type"] == "material_claim"}
                result = offers.assess(self.items[0],as_of=self.now,resolve=records.get,verify_claim=verifier(claims))
                self.assertIn(["Check power_on","Unverified or unavailable"],result["rows"])
                self.assertIn(["Reported defect display","Invented display defect"],result["rows"])

    def test_unavailable_testing_cannot_masquerade_as_same_substantive_service(self):
        with store._connection(self.fixture.path) as connection:
            records = {record["record_id"]:record for _,record in store._events(connection)}
        inspection = records["inspection-item-2"]
        next(check for check in inspection["check_results"] if check["check_id"] == "memory")["status"] = "unavailable"
        records["inspection-basis-item-2"]["subject"]["record_sha256"] = record_digest(inspection)
        claims = {identity:record for identity,record in records.items() if record["record_type"] == "material_claim"}
        with self.assertRaises(UnresolvedError):
            documentation._protocol(self.protocol,records,as_of=self.now,verify_claim=verifier(claims))


if __name__ == "__main__":
    unittest.main()
