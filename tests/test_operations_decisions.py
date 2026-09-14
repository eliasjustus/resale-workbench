"""Local synthetic human contexts; no seller, bank or purchase integration."""
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from operations import capacity, decisions, portfolio, store
from operations.errors import ConflictError, IntegrityError, UnresolvedError
from test_operations_capacity import verifier
from test_operations_service import ReadinessFixture


class LocalDecisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = fixture = ReadinessFixture(Path(self.temp.name))
        fixture.prepare()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name in ("operations.decisions.utc_now","operations.service.utc_now","operations.upstream.utc_now","operations.capacity.utc_now"):
            self.stack.enter_context(patch(name,side_effect=lambda:fixture.now))
        self.stack.enter_context(patch("operations.decisions._host_identity",return_value="synthetic-host-user"))
        self.context = decisions.authenticate(fixture.path,"actor",scope="synthetic-account",verify_claim=verifier(fixture.claims))
        fixture.put(fixture.make("review_attempt","attempt",case_id="case",actor_ref="actor",scope="synthetic-account",packet_sha256="c"*64))
        fixture.put(fixture.make("initial_judgment","judgment",case_id="case",attempt_id="attempt",actor_ref="actor",packet_sha256="c"*64))
        fixture.put(fixture.make("advice_exposure","exposure",attempt_id="attempt",actor_ref="actor",content_sha256="d"*64))

    def decide(self,**updates):
        options = {"context":self.context,"readiness_id":"ready","decision":"approve","initial_judgment_id":"judgment","exposure_id":"exposure","reason":"Synthetic human decision for tests","valid_until":"2026-01-15T12:50:00Z","record_id":"decision","request_key":"decision","verify_claim":verifier(self.fixture.claims)}
        options.update(updates)
        return decisions.decide(self.fixture.path,**options)

    def preflight(self,**updates):
        options = {"context":self.context,"decision_id":"decision","intent_id":"manual-intent","record_id":"preflight","request_key":"preflight","verify_claim":verifier(self.fixture.claims)}
        options.update(updates)
        return decisions.preflight(self.fixture.path,**options)

    def outcome(self,state,identity,prior="preflight",evidenced=False):
        record = dict(store.get_record(self.fixture.path,prior),record_id=identity,state=state,prior_attempt_id=prior,created_at=self.fixture.now,occurred_at=self.fixture.now,reconciliation_claim_ids=[identity+"-basis"] if evidenced else [])
        if evidenced:
            self.fixture.claim(record,identity+"-basis")
        return decisions.reconcile(self.fixture.path,record,context=self.context,request_key=identity,verify_claim=verifier(self.fixture.claims))

    def test_explicit_approval_and_atomic_preflight_never_perform_purchase(self):
        first = self.decide()
        self.assertEqual(first,self.decide())
        with self.assertRaises(ConflictError):
            self.decide(reason="Changed intent")
        self.fixture.now = "2026-01-15T12:31:00Z"
        receipt = self.preflight()
        self.assertEqual(receipt,self.preflight())
        view = self.fixture.resources.view()
        self.assertEqual(view["reservations"]["hold"]["state"],"committed")
        self.assertFalse(view["purchase_authorized"])
        self.assertEqual(store.get_record(self.fixture.path,"preflight")["state"],"preflight_passed")
        self.assertTrue(store.integrity_scan(self.fixture.path)["projections_current"])

    def test_context_cannot_be_replaced_by_actor_string_worker_or_revoked_scope(self):
        with self.assertRaises(IntegrityError):
            self.decide(context="actor")
        with patch("operations.decisions._host_identity",return_value="worker-principal"):
            with self.assertRaises(IntegrityError):
                decisions.authenticate(self.fixture.path,"actor",scope="synthetic-account",verify_claim=verifier(self.fixture.claims))
        self.fixture.put(dict(self.fixture.actor,record_id="revoked",created_at=self.fixture.now,revoked_at=self.fixture.now,revocation_claim_ids=["revoke"] ))
        with self.assertRaises(UnresolvedError):
            self.decide()

    def test_expired_approval_or_new_advice_requires_new_decision(self):
        self.decide()
        self.fixture.now = "2026-01-15T12:50:01Z"
        with self.assertRaises(UnresolvedError):
            self.preflight()
        self.fixture.now = "2026-01-15T12:31:00Z"
        self.fixture.put(self.fixture.make("advice_exposure","new-advice",attempt_id="attempt",actor_ref="actor",content_sha256="e"*64,created_at=self.fixture.now,exposed_at=self.fixture.now))
        with self.assertRaises(UnresolvedError):
            self.preflight()

    def test_later_decline_defer_or_approval_displaces_prior_approval(self):
        self.decide()
        for index,decision in enumerate(("decline","defer","approve")):
            self.fixture.now = "2026-01-15T12:31:00Z"
            self.decide(decision=decision,record_id="replacement-"+str(index),request_key="replacement-"+str(index))
            with self.assertRaises(UnresolvedError):
                self.preflight()
        self.assertIsNone(store.get_record(self.fixture.path,"preflight"))

    def test_changed_route_and_lost_hold_prevent_commitment(self):
        self.decide()
        self.fixture.resources.act(self.fixture.resources.action("release","cancel"))
        with self.assertRaises(UnresolvedError):
            self.preflight()
        self.assertIsNone(store.get_record(self.fixture.path,"preflight"))

    def test_failure_during_second_append_rolls_back_hold_and_attempt(self):
        self.decide()
        before = store.status(self.fixture.path)
        original = store._append
        def interrupted(connection,record,**kwargs):
            if record["record_type"] == "execution_attempt":
                raise RuntimeError("synthetic crash before second append")
            return original(connection,record,**kwargs)
        with patch("operations.store._append",side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                self.preflight()
        self.assertEqual(store.status(self.fixture.path),before)
        self.assertEqual(self.fixture.resources.view()["reservations"]["hold"]["state"],"tentative")

    def test_expiry_during_checks_prevents_a_late_commit(self):
        self.decide()
        with patch("operations.decisions.utc_now",side_effect=[self.fixture.now,"2026-01-15T12:50:01Z"]):
            with self.assertRaises(UnresolvedError):
                self.preflight()
        self.assertIsNone(store.get_record(self.fixture.path,"preflight"))
        self.assertEqual(self.fixture.resources.view()["reservations"]["hold"]["state"],"tentative")

    def test_unknown_result_cannot_retry_new_action_or_release_commitment(self):
        self.decide()
        self.preflight()
        self.outcome("outcome_unknown","unknown")
        with self.assertRaises(ConflictError):
            self.preflight(record_id="another",request_key="another",intent_id="new-intent")
        with self.assertRaises(UnresolvedError):
            self.fixture.resources.act(self.fixture.resources.action("release","unsafe-closeout"))
        self.fixture.now = "2026-01-15T13:30:00Z"
        self.assertEqual(self.fixture.resources.view()["reservations"]["hold"]["state"],"committed")
        with self.assertRaises(UnresolvedError):
            self.outcome("failed_before_action","unproven",prior="unknown")
        self.outcome("failed_before_action","proven-no-action",prior="unknown",evidenced=True)
        self.fixture.resources.act(self.fixture.resources.action("release","reviewed-closeout"))
        self.assertEqual(self.fixture.resources.view()["reservations"]["hold"]["state"],"closed")

    def test_human_pause_invalidates_prior_approval_before_commitment(self):
        self.decide()
        fixture = self.fixture
        with patch("operations.portfolio.utc_now",return_value=fixture.now):
            current = portfolio.status(fixture.path,scope="synthetic-account",synthetic=True,verify_claim=verifier(fixture.claims))
            record = fixture.make("portfolio_decision","pause",created_at=fixture.now,scope="synthetic-account",actor_ref="actor",decision="pause_intake",reason="Synthetic pause before any action",expected_resource_revision=current["resources"]["revision"],portfolio_material_sha256=current["material_sha256"])
            portfolio.record_decision(fixture.path,record,context=self.context,request_key="pause",verify_claim=verifier(fixture.claims))
        with self.assertRaises(UnresolvedError):
            self.preflight()
        self.assertEqual(fixture.resources.view()["reservations"]["hold"]["state"],"tentative")
        self.assertIsNone(store.get_record(fixture.path,"preflight"))


class NativeIdentityTests(unittest.TestCase):
    def test_actual_host_probe_ignores_user_environment_labels(self):
        first = decisions._host_identity()
        with patch.dict("os.environ",{"USER":"forged","USERNAME":"forged"}):
            self.assertEqual(decisions._host_identity(),first)
        self.assertTrue(first.startswith(("windows-sid:S-1-","posix-uid:")))


if __name__ == "__main__":
    unittest.main()
