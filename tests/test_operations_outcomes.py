"""Synthetic consecutive leads, exact cash IDs and unfinished follow-up."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from operations import cohorts, store
from operations.contracts import record_digest
from operations.errors import IntegrityError
from test_operations_capacity import RECORDS, ResourceFixture, verifier


class CohortOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.resources = ResourceFixture(self.root)
        self.path = self.resources.path
        self.now = "2026-01-01T12:30:00Z"
        self.definition = dict(deepcopy(RECORDS["cohort_definition"]),record_id="cohort",scope="synthetic-account",sampling_kind="prospective_consecutive",basis_claim_ids=["cohort-basis"])
        store.append(self.path,self.definition,request_key="cohort")
        self.resources.bind(self.definition,"cohort-basis")

    def include(self,identity,position):
        record = dict(deepcopy(RECORDS["cohort_inclusion"]),record_id="inclusion-"+identity,cohort_id=self.definition["record_id"],cohort_sha256=record_digest(self.definition),case_id=identity,scope="synthetic-account",lead_id="lead-"+identity,position=position,created_at=self.now,observed_at=self.now,basis_claim_ids=["inclusion-basis-"+identity])
        self.resources.bind(record,record["basis_claim_ids"][0])
        with patch("operations.cohorts.utc_now",return_value=self.now):
            return cohorts.include(self.path,record,request_key=record["record_id"])

    def event(self,identity,case_id,**updates):
        record = dict(deepcopy(RECORDS["outcome_event"]),record_id=identity,case_id=case_id,cohort_id=self.definition["record_id"],scope="synthetic-account",created_at=self.now,occurred_at=self.now,physical_state="not_acquired",financial_state="no_transaction",cash_delta_cents=0,cash_state="not_applicable",operator_minutes=0,measurement_basis="actual",basis_refs=[identity+"-basis"])
        record.update(updates)
        self.resources.bind(record,record["basis_refs"][0])
        store.append(self.path,record,request_key=identity)
        return record

    def cash(self,identity,delta,state="cleared"):
        self.resources.now = self.now
        action = self.resources.action("cash","cash-"+identity,reservation_id=None,cash_event_id=identity,cash_delta_cents=delta,cash_state=state,expected_resource_revision=None)
        self.resources.act(action)

    def report(self):
        return cohorts.report(self.path,self.definition["record_id"],as_of=self.now,verify_claim=verifier(self.resources.claims))

    def followup(self,case_id,identity="followup",**updates):
        material = self.report()["cases"][case_id]["outcome_material_sha256"]
        record = dict(deepcopy(RECORDS["case_followup"]),record_id=identity,cohort_id=self.definition["record_id"],case_id=case_id,scope="synthetic-account",created_at=self.now,as_of=self.now,required_followup_until=self.now,physical_closed=True,financial_closed=True,aftercare_closed=True,coverage="complete",outcome_material_sha256=material,basis_claim_ids=[identity+"-basis"])
        record.update(updates)
        self.resources.bind(record,record["basis_claim_ids"][0])
        with patch("operations.cohorts.utc_now",return_value=self.now):
            cohorts.record_followup(self.path,record,request_key=identity)

    def realized(self,case_id,identity="actual-costs",cash_allocations=(),cost_lines=None,**updates):
        case = self.report()["cases"][case_id]
        if cost_lines is None:
            cost_lines = [{"exposure_id":"transport","category":"transport","amount_cents":0,"treatment":"explicit_zero","net_receipt_cash_event_id":None,"cost_policy_id":None,"underlying_exposure_ids":["transport"],"incurred_at":self.now},{"exposure_id":"labor","category":"labor","amount_cents":1000,"treatment":"noncash","net_receipt_cash_event_id":None,"cost_policy_id":None,"underlying_exposure_ids":["operator-labor"],"incurred_at":self.now}]
        record = dict(deepcopy(RECORDS["realized_cost_review"]),record_id=identity,case_id=case_id,cohort_id=self.definition["record_id"],scope="synthetic-account",policy_id="policy",as_of=self.now,created_at=self.now,outcome_material_sha256=case["outcome_material_sha256"],measured_operator_minutes=case["known_operator_minutes"],cash_allocations=list(cash_allocations),cost_lines=cost_lines,basis_claim_ids=[identity+"-basis"])
        acquisition = [item["cash_event_id"] for item in cash_allocations if item["role"] == "acquisition"]
        record.update(acquisition_cost_cents=20000 if acquisition else 0,acquisition_basis="actual_cash_movements" if acquisition else "not_acquired" if case["physical_state"] == "not_acquired" else "reviewed_zero")
        record.update(updates)
        self.resources.bind(record,identity+"-basis")
        store.append(self.path,record,request_key=identity)
        return record

    def test_rejected_no_purchase_lead_keeps_research_cost_and_time(self):
        self.include("rejected",1)
        self.event("lead","rejected",operator_minutes=10)
        self.cash("research-cost",-500)
        self.event("unavailable","rejected",event_type="unavailable",operator_minutes=20,cash_delta_cents=-500,cash_state="cleared",cash_event_id="research-cost")
        result = self.report()
        self.assertEqual(result["funnel"]["included_leads"],1)
        self.assertEqual(result["funnel"]["acquired_cases"],0)
        self.assertEqual(result["funnel"]["known_operator_minutes"],30)
        self.assertEqual(result["funnel"]["known_cleared_cash_delta_cents"],-500)

    def test_delivered_and_payout_held_do_not_increase_cleared_cash(self):
        self.include("sale",1)
        self.cash("payout",10000,"held")
        self.event("delivery","sale",event_type="delivered",physical_state="delivered",financial_state="payout_held",cash_delta_cents=10000,cash_state="held",cash_event_id="payout")
        case = self.report()["cases"]["sale"]
        self.assertEqual((case["physical_state"],case["financial_state"]),("delivered","payout_held"))
        self.assertEqual(case["held_receivable_cents"],10000)
        self.assertEqual(case["known_cleared_cash_delta_cents"],0)
        self.assertEqual(self.resources.view()["available"]["cash_cents"],10000)
        self.followup("sale")
        self.assertNotEqual(self.report()["cases"]["sale"]["followup_status"],"closed")

    def test_return_refund_and_correction_keep_separate_states_and_original(self):
        self.include("returned-item",1)
        returned = self.event("return","returned-item",event_type="returned",physical_state="returned",financial_state="refund_pending",operator_minutes=5)
        self.now = "2026-01-01T12:35:00Z"
        self.cash("refund-debit",-4000)
        self.event("refund","returned-item",event_type="refunded",physical_state="returned",financial_state="refunded",cash_delta_cents=-4000,cash_state="cleared",cash_event_id="refund-debit",operator_minutes=10)
        self.event("time-correction","returned-item",event_type="returned",physical_state="returned",financial_state="refund_pending",operator_minutes=8,occurred_at=returned["occurred_at"],supersedes_event_id="return")
        report = self.report()
        case = report["cases"]["returned-item"]
        self.assertEqual(case["known_operator_minutes"],18)
        self.assertEqual((case["physical_state"],case["financial_state"]),("returned","refunded"))
        self.assertIn("return",report["superseded_event_ids"])
        self.assertEqual(store.get_record(self.path,"return"),returned)

    def test_open_inventory_and_missing_followup_never_become_zero_price_sale(self):
        self.include("open",1)
        self.include("unobserved",2)
        self.event("inventory","open",event_type="acquired",physical_state="owned",financial_state="unpaid",cash_delta_cents=None,cash_state="unresolved",operator_minutes=None)
        self.followup("open",coverage="censored")
        result = self.report()
        self.assertEqual(result["funnel"]["included_leads"],2)
        self.assertEqual(result["funnel"]["open_or_unfinished_cases"],2)
        self.assertEqual(result["cases"]["open"]["followup_status"],"censored")
        self.assertEqual(result["cases"]["unobserved"]["followup_status"],"missing")
        for case in result["cases"].values():
            self.assertIsNone(case["sale_price_cents"])
            self.assertIsNone(case["realized_profit_cents"])
            self.assertIsNone(case["cleared_cash_delta_cents"])

    def test_exact_cash_ids_prevent_amount_only_matches_and_duplicate_allocation(self):
        self.include("a",1)
        self.include("b",2)
        self.cash("actual",-500)
        self.event("wrong-reference","a",cash_event_id="same-amount-other-id",cash_delta_cents=-500,cash_state="cleared")
        self.assertIsNone(self.report()["cases"]["a"]["cleared_cash_delta_cents"])
        self.event("corrected","a",cash_event_id="actual",cash_delta_cents=-500,cash_state="cleared",supersedes_event_id="wrong-reference")
        self.event("duplicate","b",cash_event_id="actual",cash_delta_cents=-500,cash_state="cleared")
        result = self.report()
        self.assertIsNone(result["cases"]["a"]["cleared_cash_delta_cents"])
        self.assertIsNone(result["cases"]["b"]["cleared_cash_delta_cents"])
        self.assertEqual(result["funnel"]["known_cleared_cash_delta_cents"],0)

    def test_consecutive_inclusion_asof_and_new_export_preserve_store(self):
        receipt = self.include("first",1)
        self.assertEqual(receipt["record_id"],"inclusion-first")
        with self.assertRaises(IntegrityError):
            self.include("skipped",3)
        before = self.path.read_bytes()
        result = cohorts.report(self.path,"cohort",as_of="2026-01-01T12:29:00Z",verify_claim=verifier(self.resources.claims))
        self.assertEqual(result["funnel"]["included_leads"],0)
        destination = self.root/"cohort.json"
        cohorts.export_report(self.path,"cohort",destination,as_of=self.now,verify_claim=verifier(self.resources.claims))
        self.assertEqual(self.path.read_bytes(),before)
        with self.assertRaises(IntegrityError):
            cohorts.export_report(self.path,"cohort",destination,as_of=self.now)

    def test_reviewed_followup_closes_nonpurchase_case_but_later_event_reopens(self):
        self.include("declined",1)
        self.event("decline","declined",event_type="human_declined")
        self.followup("declined")
        self.assertEqual(self.report()["cases"]["declined"]["followup_status"],"closed")
        self.now = "2026-01-01T12:35:00Z"
        self.event("late-claim","declined",event_type="claim_opened",financial_state="claim_open")
        self.assertNotEqual(self.report()["cases"]["declined"]["followup_status"],"closed")

    def test_complete_followup_cannot_close_missing_cash_evidence(self):
        self.include("sale",1)
        self.event("delivery","sale",event_type="delivered",physical_state="delivered",financial_state="settled",cash_delta_cents=10000,cash_state="cleared",cash_event_id="missing-payout")
        self.followup("sale")
        case = self.report()["cases"]["sale"]
        self.assertNotEqual(case["followup_status"],"closed")
        self.assertIn("followup_cash_flow_unresolved",case["reason_codes"])

    def test_backdated_evidence_and_proof_changes_require_new_followup(self):
        self.include("declined",1)
        self.event("decline","declined",event_type="human_declined")
        self.followup("declined")
        self.assertEqual(self.report()["cases"]["declined"]["followup_status"],"closed")
        earlier = self.now
        self.now = "2026-01-01T12:35:00Z"
        self.event("late-research","declined",event_type="lead_observed",operator_minutes=7,occurred_at=earlier)
        case = self.report()["cases"]["declined"]
        self.assertEqual(case["known_operator_minutes"],7)
        self.assertIn("followup_material_changed",case["reason_codes"])
        self.assertNotEqual(case["followup_status"],"closed")
        self.followup("declined",identity="updated-followup")
        self.assertEqual(self.report()["cases"]["declined"]["followup_status"],"closed")
        original = verifier(self.resources.claims)
        def changed(identity,**kwargs):
            result = original(identity,**kwargs)
            if identity == "late-research-basis":
                result = dict(result,limitations=["Additional review limitation"])
            return result
        result = cohorts.report(self.path,"cohort",as_of=self.now,verify_claim=changed)
        self.assertNotEqual(result["cases"]["declined"]["followup_status"],"closed")
        self.assertIn("followup_material_changed",result["cases"]["declined"]["reason_codes"])

    def test_cash_allocation_conflicts_across_cohorts(self):
        self.include("first-case",1)
        self.cash("receipt",10000)
        self.event("first-receipt","first-case",cash_event_id="receipt",cash_delta_cents=10000,cash_state="cleared")
        self.assertEqual(self.report()["cases"]["first-case"]["cleared_cash_delta_cents"],10000)
        self.definition = dict(self.definition,record_id="second-cohort",basis_claim_ids=["second-cohort-basis"])
        store.append(self.path,self.definition,request_key="second-cohort")
        self.resources.bind(self.definition,"second-cohort-basis")
        self.include("second-case",1)
        self.event("second-receipt","second-case",cash_event_id="receipt",cash_delta_cents=10000,cash_state="cleared")
        first = cohorts.report(self.path,"cohort",as_of=self.now,verify_claim=verifier(self.resources.claims))
        second = self.report()
        self.assertIsNone(first["cases"]["first-case"]["cleared_cash_delta_cents"])
        self.assertIsNone(second["cases"]["second-case"]["cleared_cash_delta_cents"])
        self.assertEqual(first["funnel"]["known_cleared_cash_delta_cents"]+second["funnel"]["known_cleared_cash_delta_cents"],0)

    def test_held_to_cleared_payout_needs_case_allocation_and_current_proof(self):
        self.include("sale",1)
        self.cash("payout",10000,"held")
        self.event("held","sale",event_type="payment_held",physical_state="delivered",financial_state="payout_held",cash_event_id="payout",cash_delta_cents=10000,cash_state="held")
        original = verifier(self.resources.claims)
        def unverified(identity,**kwargs):
            result = original(identity,**kwargs)
            if identity == "cash-payout-basis":
                result = dict(result,state="unknown")
            return result
        case = cohorts.report(self.path,"cohort",as_of=self.now,verify_claim=unverified)["cases"]["sale"]
        self.assertIsNone(case["held_receivable_cents"])
        self.assertFalse(case["cash_flow_resolved"])
        self.now = "2026-01-01T12:35:00Z"
        action = self.resources.action("cash","cleared-payout",reservation_id=None,cash_event_id="payout",cash_delta_cents=9800,cash_state="cleared",expected_resource_revision=None)
        self.resources.act(action)
        self.event("settled","sale",event_type="payment_cleared",physical_state="delivered",financial_state="settled")
        self.followup("sale")
        case = self.report()["cases"]["sale"]
        self.assertNotEqual(case["followup_status"],"closed")
        self.assertIsNone(case["cleared_cash_delta_cents"])
        self.assertIn("cleared_payout_allocation_missing:payout",case["reason_codes"])
        self.event("allocated","sale",event_type="payment_cleared",physical_state="delivered",financial_state="settled",cash_event_id="payout",cash_delta_cents=9800,cash_state="cleared",supersedes_event_id="settled")
        self.followup("sale",identity="reconciled-followup")
        case = self.report()["cases"]["sale"]
        self.assertEqual(case["followup_status"],"closed")
        self.assertEqual(case["cleared_cash_delta_cents"],9800)

    def test_unknown_operator_time_keeps_measurement_followup_incomplete(self):
        self.include("declined",1)
        self.event("decline","declined",event_type="human_declined",operator_minutes=None)
        self.followup("declined")
        case = self.report()["cases"]["declined"]
        self.assertFalse(case["measurement_complete"])
        self.assertIn("followup_measurement_incomplete",case["reason_codes"])
        self.assertNotEqual(case["followup_status"],"closed")

    def test_closed_nopurchase_lead_includes_cash_and_noncash_research_costs(self):
        self.include("declined",1)
        self.cash("transport-debit",-500)
        self.event("decline","declined",event_type="human_declined",cash_event_id="transport-debit",cash_delta_cents=-500,cash_state="cleared",operator_minutes=30)
        self.followup("declined")
        allocation = {"cash_event_id":"transport-debit","role":"cost","category":"transport","cost_policy_id":None,"underlying_exposure_ids":["paid-transport"]}
        review = self.realized("declined",cash_allocations=[allocation])
        result = self.report()
        self.assertEqual(result["cases"]["declined"]["realized_profit_cents"],-1500)
        self.assertEqual(result["funnel"]["realized_contribution_cents"],-1500)
        self.include("unfinished",2)
        self.assertIsNone(self.report()["funnel"]["realized_contribution_cents"])
        self.assertFalse(result["claim_profitable_operation"])
        self.assertEqual(store.get_record(self.path,review["record_id"]),review)

    def test_actual_sale_contribution_uses_cleared_cash_once_and_ignores_reserve(self):
        self.include("sale",1)
        for identity,delta,kind in (("purchase",-20000,"acquired"),("transport",-5000,"inspection"),("receipt",33000,"delivered")):
            self.cash(identity,delta)
            self.event(identity,"sale",event_type=kind,cash_event_id=identity,cash_delta_cents=delta,cash_state="cleared",physical_state="delivered" if kind == "delivered" else "owned",financial_state="settled" if kind == "delivered" else "unpaid",operator_minutes=60 if identity == "transport" else 0)
        self.followup("sale")
        allocations = [{"cash_event_id":identity,"role":role,"category":"transport" if role == "cost" else None,"cost_policy_id":None,"underlying_exposure_ids":[identity]} for identity,role in (("purchase","acquisition"),("transport","cost"),("receipt","receipt"))]
        lines = [{"exposure_id":"labor","category":"labor","amount_cents":6000,"treatment":"noncash","net_receipt_cash_event_id":None,"cost_policy_id":None,"underlying_exposure_ids":["operator-labor"],"incurred_at":self.now},{"exposure_id":"liquidity","category":"transport","amount_cents":5000,"treatment":"reserve","net_receipt_cash_event_id":None,"cost_policy_id":None,"underlying_exposure_ids":["liquidity-reserve"],"incurred_at":self.now}]
        self.realized("sale",cash_allocations=allocations,cost_lines=lines)
        accounting = self.report()["cases"]["sale"]["realized_accounting"]
        self.assertEqual(accounting["cleared_cash_flow_cents"],8000)
        self.assertEqual(accounting["realized_contribution_cents"],2000)
        self.assertEqual(accounting["reserve_cents"],5000)

    def test_missing_actual_cost_and_changed_outcome_require_reconciliation(self):
        self.include("declined",1)
        self.event("decline","declined",event_type="human_declined",operator_minutes=30)
        self.followup("declined")
        self.realized("declined",measured_operator_minutes=None)
        self.assertIsNone(self.report()["cases"]["declined"]["realized_profit_cents"])
        self.realized("declined",identity="complete-costs")
        self.assertEqual(self.report()["cases"]["declined"]["realized_profit_cents"],-1000)
        self.now = "2026-01-01T12:35:00Z"
        self.event("late-work","declined",operator_minutes=10)
        self.followup("declined",identity="new-followup")
        result = self.report()["cases"]["declined"]
        self.assertIsNone(result["realized_profit_cents"])
        self.assertIn("realized_outcome_material_changed",result["realized_accounting"]["reason_codes"])

    def test_withheld_fee_is_disclosed_but_not_subtracted_twice(self):
        self.include("sale",1)
        self.cash("net-receipt",9800)
        self.event("delivery","sale",event_type="delivered",cash_event_id="net-receipt",cash_delta_cents=9800,cash_state="cleared",physical_state="delivered",financial_state="settled",operator_minutes=30)
        self.followup("sale")
        policy = dict(self.resources.policy,record_id="fee-policy",cost_coverage=["transport","labor","fees"],basis_claim_ids=["fee-policy-basis"])
        store.append(self.path,policy,request_key="fee-policy")
        self.resources.bind(policy,"fee-policy-basis")
        fee = dict(deepcopy(RECORDS["cost_policy"]),record_id="actual-fee",account_scope="synthetic-account",category="fees",rule="fixed_amount",amount_cents=200,basis_claim_ids=["actual-fee-basis"])
        store.append(self.path,fee,request_key="actual-fee")
        self.resources.bind(fee,"actual-fee-basis")
        baseline = self.realized("sale",identity="before-fee",cash_allocations=[{"cash_event_id":"net-receipt","role":"receipt","category":None,"cost_policy_id":None,"underlying_exposure_ids":["sale-receipt"]}])
        lines = baseline["cost_lines"]+[{"exposure_id":"withheld-fee","category":"fees","amount_cents":200,"treatment":"withheld","net_receipt_cash_event_id":"net-receipt","cost_policy_id":"actual-fee","underlying_exposure_ids":["fee"],"incurred_at":self.now}]
        self.realized("sale",identity="fee-accounting",policy_id="fee-policy",cash_allocations=baseline["cash_allocations"],cost_lines=lines)
        accounting = self.report()["cases"]["sale"]["realized_accounting"]
        self.assertEqual(accounting["realized_contribution_cents"],8800)
        self.assertEqual(accounting["state"],"pass",accounting)

    def test_actual_cost_allocations_cannot_overlap_or_omit_cleared_cash(self):
        self.include("declined",1)
        self.cash("research",-500)
        self.event("decline","declined",event_type="human_declined",cash_event_id="research",cash_delta_cents=-500,cash_state="cleared",operator_minutes=30)
        self.followup("declined")
        record = self.realized("declined")
        case = self.report()["cases"]["declined"]
        self.assertIsNone(case["realized_profit_cents"])
        self.assertIn("realized_cash_allocation_not_exhaustive",case["realized_accounting"]["reason_codes"])
        allocation = {"cash_event_id":"research","role":"cost","category":"transport","cost_policy_id":None,"underlying_exposure_ids":["operator-labor"]}
        self.realized("declined",identity="overlap",cash_allocations=[allocation])
        with self.assertRaises(IntegrityError):
            self.report()

    def test_unknown_acquisition_does_not_become_a_zero_cost_sale(self):
        self.include("sale",1)
        self.cash("receipt",10000)
        self.event("delivery","sale",event_type="delivered",physical_state="delivered",financial_state="settled",cash_event_id="receipt",cash_delta_cents=10000,cash_state="cleared")
        self.followup("sale")
        self.realized("sale",cash_allocations=[{"cash_event_id":"receipt","role":"receipt","category":None,"cost_policy_id":None,"underlying_exposure_ids":["receipt"]}],acquisition_cost_cents=None,acquisition_basis="unresolved")
        case = self.report()["cases"]["sale"]
        self.assertIsNone(case["realized_profit_cents"])
        self.assertIn("actual_acquisition_cost_unknown",case["realized_accounting"]["reason_codes"])

    def test_reserve_cannot_replace_required_actual_labor_cost(self):
        self.include("declined",1)
        self.event("decline","declined",event_type="human_declined",operator_minutes=30)
        self.followup("declined")
        original = self.realized("declined")
        lines = deepcopy(original["cost_lines"])
        next(line for line in lines if line["category"] == "labor")["treatment"] = "reserve"
        self.realized("declined",identity="reserve-only",cost_lines=lines)
        accounting = self.report()["cases"]["declined"]["realized_accounting"]
        self.assertIsNone(accounting["realized_contribution_cents"])
        self.assertIn("realized_cost_coverage_incomplete_or_unsupported",accounting["reason_codes"])

    def test_review_cutoff_cannot_precede_receipt_and_followup(self):
        self.include("sale",1)
        self.cash("receipt",10000)
        self.event("delivery","sale",event_type="delivered",physical_state="delivered",financial_state="settled",cash_event_id="receipt",cash_delta_cents=10000,cash_state="cleared")
        self.followup("sale")
        original = self.realized("sale",cash_allocations=[{"cash_event_id":"receipt","role":"receipt","category":None,"cost_policy_id":None,"underlying_exposure_ids":["receipt"]}])
        lines = [dict(line,incurred_at="2026-01-01T12:00:00Z") for line in original["cost_lines"]]
        self.realized("sale",identity="backdated-cutoff",as_of="2026-01-01T12:00:00Z",cash_allocations=original["cash_allocations"],cost_lines=lines)
        accounting = self.report()["cases"]["sale"]["realized_accounting"]
        self.assertIsNone(accounting["realized_contribution_cents"])
        self.assertIn("realized_cash_after_review_cutoff:receipt",accounting["reason_codes"])
        self.assertIn("realized_material_after_review_cutoff:followup",accounting["reason_codes"])

    def test_released_hold_does_not_hide_consumed_case_cash_and_time(self):
        self.include("declined",1)
        self.event("decline","declined",event_type="human_declined")
        hold = self.resources.reservation()
        hold["case_id"] = "declined"
        self.resources.reserve(hold)
        self.resources.act(self.resources.action("commit","commit"))
        self.resources.act(self.resources.action("consume","research-spend",quantities={"cash_cents":500,"work_minutes":10,"storage_units":0},cash_event_id="research-debit",cash_delta_cents=-500,cash_state="cleared",work_period="2026-01-01"))
        self.resources.act(self.resources.action("release","release"))
        self.followup("declined")
        self.realized("declined")
        case = self.report()["cases"]["declined"]
        self.assertIsNone(case["realized_profit_cents"])
        self.assertNotEqual(case["followup_status"],"closed")
        self.assertIn("case_consumption_outcome_missing:research-spend",case["reason_codes"])
        self.assertIn("case_consumed_time_not_fully_observed",case["reason_codes"])
        self.event("actual-research","declined",operator_minutes=10,cash_event_id="research-debit",cash_delta_cents=-500,cash_state="cleared")
        self.followup("declined",identity="reconciled-followup")
        self.realized("declined",identity="reconciled-costs",cash_allocations=[{"cash_event_id":"research-debit","role":"cost","category":"transport","cost_policy_id":None,"underlying_exposure_ids":["paid-research"]}])
        self.assertEqual(self.report()["cases"]["declined"]["realized_profit_cents"],-1500)

    def test_qualification_obtained_later_cannot_close_an_earlier_cost_review(self):
        self.now = "2026-01-01T12:00:00Z"
        self.include("declined",1)
        self.event("decline","declined",event_type="human_declined")
        self.followup("declined")
        earlier = self.now
        self.now = "2026-01-01T12:30:00Z"
        lines = [{"exposure_id":category,"category":category,"amount_cents":0,"treatment":"explicit_zero","net_receipt_cash_event_id":None,"cost_policy_id":None,"underlying_exposure_ids":[category],"incurred_at":earlier} for category in ("transport","labor")]
        self.realized("declined",as_of=earlier,cost_lines=lines)
        original = verifier(self.resources.claims)
        def dated(identity,**kwargs):
            result = original(identity,**kwargs)
            if identity == "followup-basis" and kwargs["as_of"] == earlier:
                result = dict(result,state="unknown")
            return result
        report = cohorts.report(self.path,"cohort",as_of=self.now,verify_claim=dated)
        self.assertEqual(report["cases"]["declined"]["followup_status"],"closed")
        accounting = report["cases"]["declined"]["realized_accounting"]
        self.assertIsNone(accounting["realized_contribution_cents"])
        self.assertIn("realized_material_not_qualified_at_review_cutoff",accounting["reason_codes"])


if __name__ == "__main__":
    unittest.main()
