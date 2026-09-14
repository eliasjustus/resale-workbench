"""Synthetic forward choices and dated human pauses preserve real ledger duties."""
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from operations import decisions, exit_review, portfolio, store
from operations.contracts import record_digest
from operations.errors import IntegrityError, UnresolvedError
from test_operations_capacity import RECORDS, ResourceFixture, verifier


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = fixture = ResourceFixture(Path(self.temp.name))
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for module in ("portfolio","decisions","capacity"):
            self.stack.enter_context(patch("operations."+module+".utc_now",side_effect=lambda:fixture.now))
        self.stack.enter_context(patch("operations.decisions._host_identity",return_value="synthetic-human"))
        actor = dict(deepcopy(RECORDS["actor_authority"]),record_id="actor",identity_ref="synthetic-human",scopes=["human_decision:synthetic-account","portfolio_control:synthetic-account"],grant_claim_ids=["grant"])
        store.append(fixture.path,actor,request_key="actor")
        fixture.bind(actor,"grant")
        self.context = decisions.authenticate(fixture.path,"actor",scope="synthetic-account",verify_claim=verifier(fixture.claims))

    def view(self):
        return portfolio.status(self.fixture.path,scope="synthetic-account",synthetic=True,verify_claim=verifier(self.fixture.claims))

    def control(self,decision,identity,**updates):
        current = self.view()
        record = dict(deepcopy(RECORDS["portfolio_decision"]),record_id=identity,created_at=self.fixture.now,scope="synthetic-account",actor_ref="actor",decision=decision,reason="Explicit synthetic human review",expected_resource_revision=current["resources"]["revision"],portfolio_material_sha256=current["material_sha256"],basis_claim_ids=[identity+"-basis"])
        record.update(updates)
        self.fixture.bind(record,identity+"-basis")
        receipt = portfolio.record_decision(self.fixture.path,record,context=self.context,request_key=identity,verify_claim=verifier(self.fixture.claims))
        return record,receipt

    def exit_review(self,identity="exit",acquisition=5000):
        record = dict(deepcopy(RECORDS["exit_review"]),record_id=identity,case_id="hold-case",scope="synthetic-account",historical_acquisition_cents=acquisition,historical_other_costs_cents=500,basis_claim_ids=[identity+"-basis"],alternatives=[{"option_id":action,"action":action,"future_receipts_cents":receipt,"future_cash_costs_cents":cost,"future_noncash_costs_cents":100,"future_work_minutes":10,"future_storage_days":days,"delay_days":days} for action,receipt,cost,days in (("hold",6000,200,10),("exit",5000,100,1))])
        store.append(self.fixture.path,record,request_key=identity)
        self.fixture.bind(record,identity+"-basis")
        return record

    def test_human_pause_blocks_new_intake_and_reviewed_resume_is_explicit(self):
        before = self.fixture.view()["available"]
        record,receipt = self.control("pause_intake","pause")
        self.assertEqual(portfolio.record_decision(self.fixture.path,record,context=self.context,request_key="pause",verify_claim=verifier(self.fixture.claims)),receipt)
        self.assertFalse(self.view()["new_intake_allowed"])
        self.assertEqual(self.fixture.view()["available"],before)
        self.assertIn("intake_paused",self.fixture.view()["reason_codes"])
        with self.assertRaises(UnresolvedError):
            self.fixture.reserve(self.fixture.reservation())
        with self.assertRaises(IntegrityError):
            store.append(self.fixture.path,dict(record,record_id="forged-resume",decision="resume_intake"),request_key="forged-resume")
        self.control("resume_intake","resume")
        self.assertTrue(self.view()["new_intake_allowed"])
        self.fixture.reserve(self.fixture.reservation())

    def test_sunk_cost_changes_history_scenario_but_not_forward_order(self):
        first = self.exit_review()
        second = self.exit_review("different-sunk-cost",acquisition=10000)
        resolve = lambda identity:store.get_record(self.fixture.path,identity)
        reports = [exit_review.assess(record,as_of=self.fixture.now,resolve=resolve,verify_claim=verifier(self.fixture.claims)) for record in (first,second)]
        self.assertEqual(reports[0]["conditional_incremental_order"],reports[1]["conditional_incremental_order"])
        for action in ("hold","exit"):
            a,b = [report["alternatives"][action] for report in reports]
            self.assertEqual(a["incremental_contribution_cents"],b["incremental_contribution_cents"])
            self.assertEqual(a["historical_inclusive_scenario_contribution_cents"]-b["historical_inclusive_scenario_contribution_cents"],5000)
            self.assertFalse(a["action_authorized"])

    def test_exit_choice_and_reporting_horizon_do_not_release_storage_or_liabilities(self):
        fixture = self.fixture
        fixture.reserve(fixture.reservation())
        fixture.act(fixture.action("commit","commit"))
        fixture.act(fixture.action("occupy","occupied",quantities={"cash_cents":0,"work_minutes":0,"storage_units":1}))
        review = self.exit_review()
        self.control("record_exit_choice","choice",exit_review_id=review["record_id"],exit_review_sha256=record_digest(review),selected_option_id="exit")
        self.control("pause_intake","pause")
        fixture.now = "2026-01-03T12:30:00Z"
        before = fixture.path.read_bytes()
        view = self.view()
        self.assertEqual(fixture.path.read_bytes(),before)
        self.assertEqual(view["resources"]["held"],{"cash_cents":10000,"work_minutes":60,"storage_units":1})
        self.assertEqual(view["cases"]["hold-case"]["occupied_units"],1)
        self.assertFalse(view["all_work_complete"])
        self.assertFalse(view["aftercare_stopped"])
        fixture.act(fixture.action("vacate","actual-vacate",quantities={"cash_cents":0,"work_minutes":0,"storage_units":1}))
        fixture.act(fixture.action("release","actual-release"))
        self.assertEqual(self.view()["resources"]["held"]["storage_units"],0)
        self.assertTrue(self.view()["intake"]["paused"])

    def test_common_account_freeze_is_not_counted_twice_and_cannot_resume(self):
        fixture = self.fixture
        hold = fixture.reservation()
        hold["resource_requests"] = {"cash_cents":6000,"work_minutes":20,"storage_units":1}
        fixture.reserve(hold)
        fixture.act(fixture.action("commit","commit"))
        exposure = dict(deepcopy(RECORDS["portfolio_case"]),record_id="exposure",case_id="hold-case",scope="synthetic-account",provider_ref="provider",account_ref="account",source_ref="source",category="desktop",correlated_failure_ref="common-account-freeze",basis_claim_ids=["exposure-basis"])
        store.append(fixture.path,exposure,request_key="exposure")
        fixture.bind(exposure,"exposure-basis")
        frozen = dict(fixture.snapshot,record_id="freeze",revision=2,active_cash_holds_cents=5000,expected_resource_revision=fixture.view()["revision"],evidence_claim_ids=["freeze-basis"])
        fixture.statement(frozen)
        self.control("pause_intake","pause")
        view = self.view()
        self.assertEqual(view["resources"]["available"]["cash_cents"],-1000)
        self.assertEqual(view["resources"]["held"]["cash_cents"],6000)
        self.assertEqual(view["concentration"]["account_ref"]["account"],["hold-case"])
        with self.assertRaises(UnresolvedError):
            self.control("resume_intake","premature-resume")
        self.assertTrue(self.view()["intake"]["paused"])

    def test_expired_resume_review_never_changes_intake_state(self):
        self.control("pause_intake","pause")
        with patch("operations.portfolio.utc_now",side_effect=[self.fixture.now,self.fixture.now,"2026-01-02T12:30:00Z"]):
            with self.assertRaises(UnresolvedError):
                self.control("resume_intake","late-resume")
        self.assertTrue(self.view()["intake"]["paused"])

    def test_backdated_request_does_not_backdate_authenticated_pause(self):
        record,_ = self.control("pause_intake","backdated",created_at="2025-01-01T12:00:00Z")
        retained = store.get_record(self.fixture.path,"backdated")
        self.assertEqual(retained["created_at"],self.fixture.now)
        self.assertIn("2025-01-01T12:00:00Z",retained["request_json"])
        self.assertFalse(portfolio.intake_state([(1,retained)],scope="synthetic-account",synthetic=True,as_of="2025-01-01T12:01:00Z")["paused"])


if __name__ == "__main__":
    unittest.main()
