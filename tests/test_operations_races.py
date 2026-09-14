"""Real competing processes, abrupt exits and bounded SQLite-full failures."""
from contextlib import contextmanager
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from operations import capacity, store
from operations.errors import OperationsError, StoreError
from test_operations_capacity import NOW, ResourceFixture, verifier


def race_action(path,action,claims,barrier,queue):
    with patch("operations.capacity.utc_now",return_value=NOW):
        barrier.wait(timeout=20)
        try:
            receipt = capacity.record_action(path,action,request_key=action["record_id"],verify_claim=verifier(claims))
            queue.put(("committed",receipt["record_id"]))
        except OperationsError as exc:
            queue.put(("refused",exc.code))


def crash_reservation(path,record,claims,before_commit):
    with patch("operations.capacity.utc_now",return_value=NOW):
        if before_commit:
            store._rebuild = lambda connection: os._exit(71)
        capacity.reserve(path,record,request_key=record["idempotency_key"],verify_claim=verifier(claims))
        os._exit(72)


class OperationalFailureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = ResourceFixture(Path(self.temp.name))
        self.context = multiprocessing.get_context("spawn")

    def join(self,process,expected):
        process.join(30)
        if process.is_alive():
            process.terminate()
            process.join()
        self.assertEqual(process.exitcode,expected)

    def test_commit_and_cancellation_race_uses_one_resource_revision(self):
        fixture = self.fixture
        fixture.reserve(fixture.reservation())
        actions = [fixture.action(operation,operation) for operation in ("commit","release")]
        for action in actions:
            fixture.bind(action,action["basis_claim_ids"][0])
        barrier,queue = self.context.Barrier(2),self.context.Queue()
        processes = [self.context.Process(target=race_action,args=(fixture.path,action,fixture.claims,barrier,queue)) for action in actions]
        for process in processes:
            process.start()
        for process in processes:
            self.join(process,0)
        results = [queue.get(timeout=5) for _ in processes]
        self.assertEqual(sum(result[0] == "committed" for result in results),1,results)
        view = fixture.view()
        state = view["reservations"]["hold"]["state"]
        self.assertIn(state,{"committed","released"})
        self.assertEqual(view["held"]["cash_cents"],10000 if state == "committed" else 0)
        self.assertEqual(sum(store.get_record(fixture.path,operation) is not None for operation in ("commit","release")),1)
        self.assertTrue(store.integrity_scan(fixture.path)["projections_current"])
        queue.close()
        queue.join_thread()

    def test_crash_before_and_after_reservation_commit_has_no_ambiguous_receipt(self):
        fixture = self.fixture
        record = fixture.reservation()
        before = store.status(fixture.path)
        process = self.context.Process(target=crash_reservation,args=(fixture.path,record,fixture.claims,True))
        process.start()
        self.join(process,71)
        store.recover(fixture.path)
        self.assertEqual(store.status(fixture.path),before)
        self.assertEqual(fixture.view()["reservations"],{})
        process = self.context.Process(target=crash_reservation,args=(fixture.path,record,fixture.claims,False))
        process.start()
        self.join(process,72)
        receipt = fixture.reserve(record)
        self.assertEqual(fixture.reserve(record),receipt)
        self.assertEqual(len(fixture.view()["reservations"]),1)
        self.assertEqual(store.status(fixture.path)["events"],before["events"]+1)
        self.assertTrue(store.integrity_scan(fixture.path)["projections_current"])

    def test_actual_sqlite_full_rolls_back_all_resources_and_receipt(self):
        fixture = self.fixture
        original_connection = store._connection
        @contextmanager
        def constrained(path,**kwargs):
            with original_connection(path,**kwargs) as connection:
                if kwargs.get("write"):
                    count = connection.execute("PRAGMA page_count").fetchone()[0]
                    self.assertEqual(connection.execute("PRAGMA max_page_count="+str(count)).fetchone()[0],count)
                yield connection
        request = fixture.reservation()
        oversized = dict(request,producer_ref="synthetic-full-database-probe-"+"x"*262144)
        before = store.status(fixture.path)
        with patch("operations.store._connection",constrained):
            with self.assertRaises(StoreError) as caught:
                fixture.reserve(oversized)
        self.assertEqual(caught.exception.__cause__.sqlite_errorcode,sqlite3.SQLITE_FULL)
        self.assertEqual(store.status(fixture.path),before)
        self.assertEqual(fixture.view()["reservations"],{})
        receipt = fixture.reserve(request)
        self.assertEqual(fixture.reserve(request),receipt)
        self.assertEqual(len(fixture.view()["reservations"]),1)
        self.assertTrue(store.integrity_scan(fixture.path)["projections_current"])


if __name__ == "__main__":
    unittest.main()
