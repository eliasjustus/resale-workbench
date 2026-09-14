"""Offline integrated shocks; synthetic claim verification is not live authority."""
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from operations import capacity, cohorts, decisions, portfolio, store
from operations.contracts import record_digest
from operations.errors import UnresolvedError
from test_operations_capacity import ResourceFixture, verifier
from test_operations_service import ReadinessFixture


class IntegratedStressTests(unittest.TestCase):
    def test_positive_economics_cannot_override_one_bad_route_fact(self):
        for changes in ({},{"protection_status":"unavailable"},{"item_party_payment_binding":"unresolved"}):
            with self.subTest(changes=changes),tempfile.TemporaryDirectory() as directory:
                fixture = ReadinessFixture(Path(directory),route_changes=changes)
                result = fixture.assess()
                self.assertEqual(result["checks"]["operating_scenario"]["state"],"pass")
                self.assertEqual(result["checks"]["current_economics"]["state"],"pass")
                if changes:
                    self.assertNotEqual(result["status"],"ready_for_human_review")
                    self.assertNotEqual(result["checks"]["route"]["state"],"pass")
                else:
                    self.assertEqual(result["status"],"ready_for_human_review",result["checks"])
                    fixture.prepare()
                    self.assertIsNotNone(store.get_record(fixture.path,"ready"))
                self.assertFalse(result["purchase_authorized"])

    def test_shared_freeze_partial_consumption_and_return_preserve_actual_exposure(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = ResourceFixture(Path(directory))
            expanded = dict(fixture.snapshot,record_id="expanded",revision=2,work_minutes=120,storage_units=2,expected_resource_revision=fixture.view()["revision"],evidence_claim_ids=["expanded-basis"])
            fixture.statement(expanded)
            for identity in ("first","second"):
                hold = fixture.reservation(identity)
                hold["resource_requests"] = {"cash_cents":3000,"work_minutes":30,"storage_units":1}
                fixture.reserve(hold)
                fixture.act(fixture.action("commit","commit-"+identity,reservation_id=identity))
            self.assertEqual(fixture.view()["available"]["cash_cents"],4000)
            fixture.act(fixture.action("cash","held-payout",reservation_id=None,cash_event_id="held-payout",cash_delta_cents=9000,cash_state="held",expected_resource_revision=None))
            self.assertEqual(fixture.view()["available"]["cash_cents"],4000)
            frozen = dict(fixture.snapshot,record_id="freeze",revision=3,active_cash_holds_cents=3000,expected_resource_revision=fixture.view()["revision"],evidence_claim_ids=["freeze-basis"])
            fixture.statement(frozen)
            self.assertEqual(fixture.view()["available"]["cash_cents"],1000)
            self.assertEqual(fixture.view()["held"]["cash_cents"],6000)
            fixture.act(fixture.action("consume","partial",reservation_id="first",quantities={"cash_cents":500,"work_minutes":10,"storage_units":0},cash_event_id="partial-debit",cash_delta_cents=-500,cash_state="cleared",work_period="2026-01-01"))
            self.assertEqual(fixture.view()["available"]["cash_cents"],1000)
            fixture.act(fixture.action("cash","return-debit",reservation_id=None,cash_event_id="return-debit",cash_delta_cents=-2000,cash_state="cleared",expected_resource_revision=None))
            view = fixture.view()
            self.assertEqual(view["available"]["cash_cents"],-1000)
            request = fixture.reservation("new-case")
            request["resource_requests"] = {"cash_cents":1,"work_minutes":0,"storage_units":0}
            with self.assertRaises(UnresolvedError):
                fixture.reserve(request)
            self.assertEqual(view["held"],{"cash_cents":5500,"work_minutes":50,"storage_units":2})
            self.assertTrue(all(hold["state"] == "committed" for hold in view["reservations"].values()))
            self.assertTrue(store.integrity_scan(fixture.path)["projections_current"])

    def test_intake_stop_and_reporting_horizon_keep_unknown_attempt_and_aftercare(self):
        with tempfile.TemporaryDirectory() as directory,ExitStack() as stack:
            fixture = ReadinessFixture(Path(directory))
            fixture.prepare()
            for name in ("decisions","service","upstream","capacity","cohorts","portfolio"):
                stack.enter_context(patch("operations."+name+".utc_now",side_effect=lambda:fixture.now))
            stack.enter_context(patch("operations.decisions._host_identity",return_value="synthetic-host-user"))
            context = decisions.authenticate(fixture.path,"actor",scope="synthetic-account",verify_claim=verifier(fixture.claims))
            for record in (fixture.make("review_attempt","attempt",case_id="case",actor_ref="actor",scope="synthetic-account",packet_sha256="c"*64),fixture.make("initial_judgment","judgment",case_id="case",attempt_id="attempt",actor_ref="actor",packet_sha256="c"*64),fixture.make("advice_exposure","exposure",attempt_id="attempt",actor_ref="actor",content_sha256="d"*64)):
                fixture.put(record)
            decisions.decide(fixture.path,context=context,readiness_id="ready",decision="approve",initial_judgment_id="judgment",exposure_id="exposure",reason="Synthetic stress rehearsal",valid_until="2026-01-15T12:50:00Z",record_id="decision",request_key="decision",verify_claim=verifier(fixture.claims))
            decisions.preflight(fixture.path,context=context,decision_id="decision",intent_id="intent",record_id="preflight",request_key="preflight",verify_claim=verifier(fixture.claims))
            unknown = dict(store.get_record(fixture.path,"preflight"),record_id="unknown",state="outcome_unknown",prior_attempt_id="preflight",created_at=fixture.now,occurred_at=fixture.now,reconciliation_claim_ids=[])
            decisions.reconcile(fixture.path,unknown,context=context,request_key="unknown",verify_claim=verifier(fixture.claims))
            current = portfolio.status(fixture.path,scope="synthetic-account",synthetic=True,verify_claim=verifier(fixture.claims))
            pause = fixture.make("portfolio_decision","pause",created_at=fixture.now,scope="synthetic-account",actor_ref="actor",decision="pause_intake",reason="Dated synthetic intake pause",expected_resource_revision=current["resources"]["revision"],portfolio_material_sha256=current["material_sha256"])
            portfolio.record_decision(fixture.path,pause,context=context,request_key="pause",verify_claim=verifier(fixture.claims))
            definition = fixture.make("cohort_definition","cohort",scope="synthetic-account",intake_ended_at=fixture.now,basis_claim_ids=["cohort-basis"])
            fixture.put(definition)
            fixture.claim(definition,"cohort-basis")
            inclusion = fixture.make("cohort_inclusion","inclusion",cohort_id="cohort",cohort_sha256=record_digest(definition),case_id="case",scope="synthetic-account",position=1,created_at=fixture.now,observed_at=fixture.now,basis_claim_ids=["inclusion-basis"])
            fixture.claim(inclusion,"inclusion-basis")
            cohorts.include(fixture.path,inclusion,request_key="inclusion")
            event = fixture.make("outcome_event","claim",case_id="case",cohort_id="cohort",scope="synthetic-account",event_type="claim_opened",physical_state="unknown",financial_state="claim_open",created_at=fixture.now,occurred_at=fixture.now,basis_refs=["claim-basis"])
            fixture.claim(event,"claim-basis")
            fixture.put(event)
            snapshot = dict(fixture.resources.snapshot,record_id="stop-intake",revision=3,cleared_cash_cents=None,expected_resource_revision=fixture.resources.view()["revision"],evidence_claim_ids=["stop-intake-basis"])
            fixture.resources.statement(snapshot)
            fixture.now = "2026-01-17T12:30:00Z"
            fixture.resources.now = fixture.now
            before = fixture.path.read_bytes()
            view = fixture.resources.view()
            report = cohorts.report(fixture.path,"cohort",as_of=fixture.now,verify_claim=verifier(fixture.claims))
            self.assertEqual(fixture.path.read_bytes(),before)
            self.assertEqual(view["reservations"]["hold"]["state"],"committed")
            self.assertEqual(view["held"]["cash_cents"],25000)
            self.assertEqual(report["cases"]["case"]["financial_state"],"claim_open")
            self.assertEqual(report["funnel"]["open_or_unfinished_cases"],1)
            self.assertEqual(store.get_record(fixture.path,"unknown"),unknown)
            self.assertTrue(portfolio.status(fixture.path,scope="synthetic-account",synthetic=True,verify_claim=verifier(fixture.claims))["intake"]["paused"])
            with self.assertRaises(UnresolvedError):
                fixture.resources.act(fixture.resources.action("release","premature-release"))


if __name__ == "__main__":
    unittest.main()
