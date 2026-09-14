"""Invented local resources; review callbacks are synthetic test doubles only."""
from copy import deepcopy
from contextlib import closing
import json
import multiprocessing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from operations import capacity, store
from operations.contracts import record_digest
from operations.errors import ConflictError, IntegrityError, OperationsError, UnresolvedError

RECORDS = json.loads((Path(__file__).parent/"fixtures/operations-records.json").read_text(encoding="utf-8"))
NOW = "2026-01-01T12:30:00Z"


def verifier(records):
    def verify(identity,**kwargs):
        claim = records[identity]
        return {"state":"pass","claim_id":identity,"claim_sha256":record_digest(claim),"subject":claim["subject"],"limitations":[],"review_limitations":[]}
    return verify


def compete(path,record,claims,barrier,queue):
    with patch("operations.capacity.utc_now",return_value=NOW):
        barrier.wait(timeout=20)
        try:
            receipt = capacity.reserve(path,record,request_key=record["idempotency_key"],verify_claim=verifier(claims))
            queue.put(("committed",receipt["record_id"]))
        except OperationsError as exc:
            queue.put(("refused",exc.code))


class ResourceFixture:
    def __init__(self,root):
        self.path = store.initialize(root/"operations")
        self.claims = {}
        self.now = NOW
        self.policy = dict(deepcopy(RECORDS["operating_policy"]),record_id="policy",scope="synthetic-account",valid_until="2026-02-01T12:00:00Z",authority_refs=["synthetic-actor"],source_authority_refs=["synthetic-source"],cash_floor_cents=0,incremental_stress_cents=0,labor_cents_per_hour=2000,minimum_contribution_cents=2000,cost_coverage=["transport","labor"],contribution_rule="receipts_minus_all_costs",labor_allocation_rule="reviewed fixed workload",research_allocation_rule="reviewed allocation",deadline_policy="manual reviewed deadlines",clock_skew_seconds=0,validity_seconds=3600,basis_claim_ids=["policy-basis"])
        store.append(self.path,self.policy,request_key="policy")
        self.bind(self.policy,"policy-basis")
        self.snapshot = dict(deepcopy(RECORDS["capacity_snapshot"]),record_id="snapshot",revision=1,scope="synthetic-account",policy_id="policy",work_period="2026-01-01",bank_watermark="fictional-statement-1",cleared_cash_cents=10000,obligations_cents=0,cash_floor_cents=0,incremental_stress_cents=0,active_cash_holds_cents=0,work_minutes=60,storage_units=1,evidence_claim_ids=["snapshot-basis"])
        self.statement(self.snapshot)

    def bind(self,target,identity):
        claim = dict(deepcopy(RECORDS["material_claim"]),record_id=identity,subject={"record_id":target["record_id"],"record_type":target["record_type"],"record_sha256":record_digest(target),"verified_fields":list(target)})
        self.claims[identity] = claim
        store.append(self.path,claim,request_key=identity)

    def statement(self,record):
        self.bind(record,record["evidence_claim_ids"][0])
        capacity.import_snapshot(self.path,record,request_key=record["record_id"])
        self.snapshot = record

    def view(self,qualified=True):
        with patch("operations.capacity.utc_now",return_value=self.now):
            return capacity.status(self.path,scope="synthetic-account",synthetic=True,verify_claim=verifier(self.claims) if qualified else None)

    def reservation(self,identity="hold",**updates):
        return dict(deepcopy(RECORDS["reservation"]),record_id=identity,case_id=identity+"-case",reservation_id=identity,idempotency_key=identity,capacity_snapshot_id=self.snapshot["record_id"],scope="synthetic-account",created_at=self.now,expires_at="2026-01-01T13:00:00Z",expected_resource_revision=self.view()["revision"],resource_requests={"cash_cents":10000,"work_minutes":60,"storage_units":1},**updates)

    def reserve(self,record):
        with patch("operations.capacity.utc_now",return_value=self.now):
            return capacity.reserve(self.path,record,request_key=record["idempotency_key"],verify_claim=verifier(self.claims))

    def action(self,operation,identity,**updates):
        record = dict(deepcopy(RECORDS["resource_action"]),record_id=identity,scope="synthetic-account",reservation_id="hold",operation=operation,created_at=self.now,occurred_at=self.now,expected_resource_revision=self.view()["revision"],basis_claim_ids=[identity+"-basis"])
        record.update(updates)
        return record

    def act(self,record):
        self.bind(record,record["basis_claim_ids"][0])
        with patch("operations.capacity.utc_now",return_value=self.now):
            return capacity.record_action(self.path,record,request_key=record["record_id"],verify_claim=verifier(self.claims))


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = ResourceFixture(Path(self.temp.name))

    def test_two_processes_cannot_partially_reserve_last_resources(self):
        fixture = self.fixture
        context = multiprocessing.get_context("spawn")
        barrier,queue = context.Barrier(2),context.Queue()
        processes = [context.Process(target=compete,args=(fixture.path,fixture.reservation(identity),fixture.claims,barrier,queue)) for identity in ("first","second")]
        for process in processes:
            process.start()
        for process in processes:
            process.join(30)
            if process.is_alive():
                process.terminate()
                process.join()
            self.assertEqual(process.exitcode,0)
        results = [queue.get(timeout=5) for _ in processes]
        self.assertEqual(sum(result[0] == "committed" for result in results),1,results)
        view = fixture.view()
        self.assertEqual(len(view["reservations"]),1)
        self.assertEqual(view["available"],dict.fromkeys(capacity.RESOURCES,0))
        self.assertTrue(store.integrity_scan(fixture.path)["projections_current"])

    def test_retry_does_not_reactivate_expired_hold_and_changed_payload_conflicts(self):
        fixture = self.fixture
        request = fixture.reservation()
        receipt = fixture.reserve(request)
        fixture.now = "2026-01-01T13:00:01Z"
        self.assertEqual(fixture.reserve(request),receipt)
        self.assertEqual(fixture.view()["held"],dict.fromkeys(capacity.RESOURCES,0))
        self.assertEqual(fixture.view()["reservations"]["hold"]["state"],"expired")
        changed = dict(request,expires_at="2026-01-01T13:10:00Z")
        with self.assertRaises(ConflictError):
            fixture.reserve(changed)
        with self.assertRaises(UnresolvedError):
            fixture.act(fixture.action("commit","expired-commit"))

    def test_consumption_reduces_stock_and_hold_storage_survives_expiry(self):
        fixture = self.fixture
        fixture.reserve(fixture.reservation())
        fixture.act(fixture.action("commit","commit"))
        fixture.now = "2026-01-01T12:35:00Z"
        fixture.act(fixture.action("consume","spend",quantities={"cash_cents":4000,"work_minutes":20,"storage_units":0},cash_event_id="bank-debit",cash_delta_cents=-4000,cash_state="cleared",work_period="2026-01-01"))
        fixture.act(fixture.action("occupy","shelve",quantities={"cash_cents":0,"work_minutes":0,"storage_units":1}))
        fixture.now = "2026-01-01T14:00:00Z"
        view = fixture.view()
        self.assertEqual(view["cleared_cash_preview_cents"],6000)
        self.assertEqual(view["held"],{"cash_cents":6000,"work_minutes":40,"storage_units":1})
        self.assertEqual(view["available"],dict.fromkeys(capacity.RESOURCES,0))
        with self.assertRaises(IntegrityError):
            fixture.act(fixture.action("release","occupied-closeout"))
        fixture.act(fixture.action("vacate","vacate",quantities={"cash_cents":0,"work_minutes":0,"storage_units":1}))
        fixture.act(fixture.action("release","closeout"))
        self.assertEqual(fixture.view()["available"],{"cash_cents":6000,"work_minutes":40,"storage_units":1})

    def test_unknown_evidence_stale_revision_and_short_resource_roll_back(self):
        fixture = self.fixture
        record = fixture.reservation()
        before = store.status(fixture.path)
        with patch("operations.capacity.utc_now",return_value=NOW):
            with self.assertRaises(UnresolvedError):
                capacity.reserve(fixture.path,record,request_key="hold")
        record["expected_resource_revision"] = 0
        with self.assertRaises(IntegrityError):
            fixture.reserve(record)
        record["expected_resource_revision"] = 1
        record["resource_requests"]["storage_units"] = 2
        with self.assertRaises(UnresolvedError):
            fixture.reserve(record)
        self.assertEqual(store.status(fixture.path),before)
        with self.assertRaises(IntegrityError):
            store.append(fixture.path,record,request_key="bypass")
        self.assertNotEqual(fixture.view(qualified=False)["state"],"pass")

    def test_resource_projection_is_disposable_and_read_status_does_not_write(self):
        fixture = self.fixture
        fixture.reserve(fixture.reservation())
        expected = fixture.view()
        with closing(sqlite3.connect(fixture.path)) as connection:
            connection.execute("DELETE FROM resource_projection")
            connection.commit()
        before = fixture.path.read_bytes()
        self.assertEqual(fixture.view(),expected)
        self.assertEqual(fixture.path.read_bytes(),before)
        self.assertFalse(store.integrity_scan(fixture.path)["projections_current"])
        store.rebuild_projections(fixture.path)
        self.assertTrue(store.integrity_scan(fixture.path)["projections_current"])

    def test_period_rollover_preserves_commitment_and_late_work_uses_original_period(self):
        fixture = self.fixture
        fixture.reserve(fixture.reservation())
        fixture.act(fixture.action("commit","commit"))
        fixture.now = "2026-01-02T12:30:00Z"
        snapshot = dict(fixture.snapshot,record_id="day-two",revision=2,created_at=fixture.now,as_of=fixture.now,valid_until="2026-01-03T12:30:00Z",work_period="2026-01-02",expected_resource_revision=fixture.view()["revision"],evidence_claim_ids=["day-two-basis"])
        fixture.statement(snapshot)
        fixture.act(fixture.action("consume","late-work",quantities={"cash_cents":0,"work_minutes":20,"storage_units":0},work_period="2026-01-01",occurred_at="2026-01-01T12:40:00Z"))
        self.assertEqual(fixture.view()["held"]["work_minutes"],40)
        self.assertEqual(fixture.view()["available"]["work_minutes"],20)
        self.assertEqual(fixture.view()["held"]["storage_units"],1)

    def test_commit_rechecks_capacity_and_expiry_cannot_exceed_policy(self):
        fixture = self.fixture
        request = fixture.reservation()
        with self.assertRaises(UnresolvedError):
            fixture.reserve(dict(request,expires_at="2026-01-01T14:00:00Z"))
        fixture.reserve(request)
        snapshot = dict(fixture.snapshot,record_id="unknown",revision=2,expected_resource_revision=fixture.view()["revision"],cleared_cash_cents=None,evidence_claim_ids=["unknown-basis"])
        fixture.statement(snapshot)
        with self.assertRaises(UnresolvedError):
            fixture.act(fixture.action("commit","unqualified-commit"))

    def test_policy_review_provenance_is_included_in_capacity_binding(self):
        fixture = self.fixture
        original = verifier(fixture.claims)
        def assessed(version):
            def versioned(identity,**kwargs):
                result = original(identity,**kwargs)
                if identity == "policy-basis":
                    result["review_ids"] = [version]
                return result
            with patch("operations.capacity.utc_now",return_value=fixture.now):
                return capacity.status(fixture.path,scope="synthetic-account",synthetic=True,verify_claim=versioned)
        first,second = assessed("first"),assessed("second")
        self.assertEqual(first["state"],"pass")
        self.assertIn("policy-basis",first["input_digests"])
        self.assertNotEqual(first["verification_digests"],second["verification_digests"])

    def test_expiry_during_verification_does_not_create_a_late_hold(self):
        fixture = self.fixture
        request = fixture.reservation()
        with patch("operations.capacity.utc_now",side_effect=[fixture.now,"2026-01-01T13:00:01Z"]):
            with self.assertRaises(UnresolvedError):
                capacity.reserve(fixture.path,request,request_key="hold",verify_claim=verifier(fixture.claims))
        self.assertEqual(fixture.view()["reservations"],{})


if __name__ == "__main__":
    unittest.main()
