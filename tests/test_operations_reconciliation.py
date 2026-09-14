"""Synthetic bank-first/event-first reconciliation and negative reality."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from operations import capacity, store
from operations.errors import ConflictError, IntegrityError, UnresolvedError
from test_operations_capacity import ResourceFixture, verifier


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = ResourceFixture(Path(self.temp.name))

    def cash(self,identity,delta,state="cleared",**updates):
        fixture = self.fixture
        event = fixture.action("cash",identity,reservation_id=None,cash_event_id=identity,cash_delta_cents=delta,cash_state=state,expected_resource_revision=None)
        event.update(updates)
        return event

    def statement(self,identity,amount,reflected):
        fixture = self.fixture
        return dict(fixture.snapshot,record_id=identity,revision=fixture.snapshot["revision"]+1,as_of=fixture.now,created_at=fixture.now,cleared_cash_cents=amount,reflected_cash_ids=reflected,expected_resource_revision=fixture.view()["revision"],evidence_claim_ids=[identity+"-basis"])

    def test_held_payout_is_not_cash_and_clearing_once_is_not_duplicate_income(self):
        fixture = self.fixture
        fixture.act(self.cash("payout",5000,"held"))
        self.assertEqual(fixture.view()["available"]["cash_cents"],10000)
        fixture.now = "2026-01-01T12:31:00Z"
        fixture.act(self.cash("payout-cleared",4800,cash_event_id="payout"))
        self.assertEqual(fixture.view()["available"]["cash_cents"],14800)
        with self.assertRaises(ConflictError):
            fixture.act(self.cash("duplicate",4800,cash_event_id="payout"))
        self.assertEqual(fixture.view()["available"]["cash_cents"],14800)

    def test_debit_then_statement_then_hold_consumption_does_not_debit_twice(self):
        fixture = self.fixture
        fixture.reserve(fixture.reservation())
        fixture.act(fixture.action("commit","commit"))
        debit = self.cash("debit",-4000)
        fixture.act(debit)
        self.assertEqual(fixture.view()["cleared_cash_preview_cents"],6000)
        fixture.now = "2026-01-01T12:35:00Z"
        fixture.statement(self.statement("bank",6000,["debit"]))
        self.assertEqual(fixture.view()["cleared_cash_preview_cents"],6000)
        fixture.act(fixture.action("consume","spend",quantities={"cash_cents":4000,"work_minutes":0,"storage_units":0},cash_event_id="debit",cash_delta_cents=-4000,cash_state="cleared",occurred_at=debit["occurred_at"]))
        view = fixture.view()
        self.assertEqual(view["cleared_cash_preview_cents"],6000)
        self.assertEqual(view["available"]["cash_cents"],0)
        with self.assertRaises(ConflictError):
            fixture.act(fixture.action("consume","duplicate-spend",quantities={"cash_cents":4000,"work_minutes":0,"storage_units":0},cash_event_id="debit",cash_delta_cents=-4000,cash_state="cleared",occurred_at=debit["occurred_at"]))

    def test_statement_first_reflected_debit_does_not_debit_twice(self):
        fixture = self.fixture
        fixture.statement(self.statement("bank",6000,["debit"]))
        fixture.now = "2026-01-01T12:35:00Z"
        fixture.act(self.cash("debit",-4000,occurred_at="2026-01-01T12:30:00Z"))
        self.assertEqual(fixture.view()["available"]["cash_cents"],6000)
        with self.assertRaises(IntegrityError):
            fixture.statement(self.statement("forgotten",6000,[]))

    def test_adverse_real_shape_fact_is_retained_in_synthetic_test_and_blocks_intake(self):
        fixture = self.fixture
        event = self.cash("unexpected-debit",-15000)
        fixture.act(event)
        self.assertEqual(store.get_record(fixture.path,"unexpected-debit"),event)
        self.assertEqual(fixture.view()["available"]["cash_cents"],-5000)
        self.assertEqual(fixture.view()["state"],"fail")
        with self.assertRaises(UnresolvedError):
            fixture.reserve(fixture.reservation())
        self.assertTrue(store.integrity_scan(fixture.path)["projections_current"])

    def test_unknown_cash_remains_unknown_and_reflection_time_conflict_abstains(self):
        fixture = self.fixture
        fixture.act(self.cash("unknown-debit",None))
        self.assertIsNone(fixture.view()["available"]["cash_cents"])
        fixture.statement(self.statement("bank",6000,["unknown-debit","later"]))
        fixture.now = "2026-01-01T12:35:00Z"
        fixture.act(self.cash("later",-400))
        self.assertIn("cash_reflection_time_conflict",fixture.view()["reason_codes"])
        self.assertIsNone(fixture.view()["available"]["cash_cents"])

    def test_pre_statement_unmatched_cash_requires_explicit_reconciliation(self):
        fixture = self.fixture
        fixture.act(self.cash("old-income",5000,occurred_at="2026-01-01T11:00:00Z"))
        self.assertIn("cash_before_watermark_unreconciled",fixture.view()["reason_codes"])
        with self.assertRaises(UnresolvedError):
            fixture.reserve(fixture.reservation())
        fixture.statement(self.statement("reviewed-bank",10000,["old-income"]))
        self.assertEqual(fixture.view()["available"]["cash_cents"],10000)

    def test_new_intake_rechecks_unreconciled_cash_evidence(self):
        fixture = self.fixture
        fixture.act(self.cash("income",5000))
        original = verifier(fixture.claims)
        def unavailable(identity,**kwargs):
            return dict(original(identity,**kwargs),state="unknown") if identity == "income-basis" else original(identity,**kwargs)
        request = fixture.reservation()
        request["resource_requests"]["cash_cents"] = 15000
        with patch("operations.capacity.utc_now",return_value=fixture.now):
            view = capacity.status(fixture.path,scope="synthetic-account",synthetic=True,verify_claim=unavailable)
            self.assertEqual(view["cleared_cash_preview_cents"],15000)
            self.assertEqual(view["state"],"unknown")
            with self.assertRaises(UnresolvedError):
                capacity.reserve(fixture.path,request,request_key="hold",verify_claim=unavailable)
        self.assertIsNotNone(store.get_record(fixture.path,"income"))


if __name__ == "__main__":
    unittest.main()
