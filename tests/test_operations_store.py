"""Synthetic transaction, crash, replay and recovery tests for the private store."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from operations import store
from operations.errors import BusyError, ConflictError, IntegrityError, StoreError, VersionError

RECORDS = json.loads((Path(__file__).parent / "fixtures/operations-records.json").read_text(encoding="utf-8"))


class OperationsStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = store.initialize(self.root / "private")

    def outcome(self, name, cash, **changes):
        return dict(deepcopy(RECORDS["outcome_event"]), record_id=name, cash_delta_cents=cash, **changes)

    def test_concurrent_identical_replay_is_one_receipt_and_changed_payload_conflicts(self):
        record = self.outcome("one", -5000)
        with ThreadPoolExecutor(max_workers=4) as pool:
            receipts = list(pool.map(lambda _: store.append(self.path, record, request_key="request-one"), range(4)))
        self.assertTrue(all(value == receipts[0] for value in receipts))
        self.assertEqual(store.status(self.path)["events"], 1)
        with self.assertRaises(ConflictError):
            store.append(self.path, dict(record, cash_delta_cents=-5100), request_key="request-one")
        with self.assertRaises(ConflictError):
            store.append(self.path, record, request_key="another-request")
        self.assertTrue(store.integrity_scan(self.path)["projections_current"])

    def test_crash_before_commit_rolls_back_and_after_commit_can_regenerate_export(self):
        record_path = self.root / "synthetic.json"
        record_path.write_text(json.dumps(self.outcome("crash", -100)), encoding="utf-8")
        before = """
import json, os, sys
from operations import store
record = json.load(open(sys.argv[2], encoding='utf-8'))
store._rebuild = lambda connection: os._exit(71)
store.append(sys.argv[1], record, request_key='crash-key')
"""
        result = subprocess.run([sys.executable, "-c", before, str(self.path), str(record_path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 71, result.stderr)
        store.recover(self.path)
        self.assertEqual(store.status(self.path)["events"], 0)
        after = """
import json, os, sys
from operations import store
record = json.load(open(sys.argv[2], encoding='utf-8'))
store.append(sys.argv[1], record, request_key='crash-key')
os._exit(72)
"""
        result = subprocess.run([sys.executable, "-c", after, str(self.path), str(record_path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 72, result.stderr)
        receipt = store.append(self.path, self.outcome("crash", -100), request_key="crash-key")
        self.assertEqual(receipt["sequence"], 1)
        exported = store.export_records(self.path, self.root / "recovered-export.json")
        self.assertEqual(json.loads(exported.read_text(encoding="utf-8"))["records"], [self.outcome("crash", -100)])
        self.assertEqual(store.status(self.path)["events"], 1)
        with self.assertRaises(StoreError):
            store.export_records(self.path, exported)
        self.assertTrue(store.integrity_scan(self.path)["projections_current"])

    def test_rebuild_preserves_unknowns_negative_facts_and_append_only_corrections(self):
        store.append(self.path, self.outcome("acquired", -5000), request_key="1")
        store.append(self.path, self.outcome("unknown", None), request_key="2")
        store.append(self.path, self.outcome("corrected", -200, supersedes_event_id="unknown"), request_key="3")
        expected = store.status(self.path)
        self.assertEqual(expected["cases"][0]["known_cash_delta_cents"], -5200)
        self.assertEqual(expected["cases"][0]["unknown_cash_events"], 0)
        self.assertEqual(expected["cases"][0]["unknown_time_events"], 2)
        self.assertIsNone(store.get_record(self.path, "unknown")["cash_delta_cents"])
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("DELETE FROM record_projection")
            connection.execute("DELETE FROM case_projection")
            connection.execute("DELETE FROM projection_versions")
            connection.commit()
        finally:
            connection.close()
        self.assertFalse(store.integrity_scan(self.path)["projections_current"])
        self.assertEqual(store.status(self.path), expected)
        store.rebuild_projections(self.path)
        self.assertTrue(store.integrity_scan(self.path)["projections_current"])
        self.assertEqual(store.status(self.path), expected)
        for previous, extra in (("absent", {}), ("unknown", {}), ("acquired", {"case_id":"different-case"})):
            with self.assertRaises(IntegrityError):
                store.append(self.path, self.outcome("bad", 0, supersedes_event_id=previous, **extra), request_key="bad")
        self.assertEqual(store.status(self.path), expected)

    def test_history_rejects_sql_mutations_and_detects_tampering(self):
        store.append(self.path, self.outcome("one", 100), request_key="one")
        connection = sqlite3.connect(self.path)
        try:
            for table in ("records", "operational_events", "request_receipts"):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(f"DELETE FROM {table}")
            connection.execute("DROP TRIGGER immutable_records_update")
            connection.execute("UPDATE records SET digest=?", ("0" * 64,))
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(IntegrityError):
            store.integrity_scan(self.path)
        with self.assertRaises(IntegrityError):
            store.get_record(self.path, "one")

    def test_late_arrival_uses_event_time_and_cannot_mix_synthetic_cases(self):
        store.append(self.path, self.outcome("latest", 500, occurred_at="2026-01-01T11:00:00Z", physical_state="sold"), request_key="latest")
        store.append(self.path, self.outcome("earlier", -300, occurred_at="2026-01-01T10:00:00Z", physical_state="acquired"), request_key="earlier")
        case = store.status(self.path)["cases"][0]
        self.assertEqual(case["physical_state"], "sold")
        self.assertEqual(case["known_cash_delta_cents"], 200)
        with self.assertRaises(IntegrityError):
            store.append(self.path, self.outcome("mixed", 20, synthetic=False), request_key="mixed")
        self.assertEqual(store.status(self.path)["events"], 2)

    def test_short_busy_failure_commits_nothing(self):
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            with self.assertRaises(BusyError):
                store.append(self.path, self.outcome("busy", 1), request_key="busy", timeout=0.01)
        finally:
            connection.close()
        self.assertEqual(store.status(self.path)["events"], 0)

    def test_contract_snapshot_mismatch_and_initialization_errors_are_explicit(self):
        with patch.object(store, "schema_snapshot", return_value={"different":"schema"}):
            other = store.initialize(self.root / "different")
        for action in (store.status, store.integrity_scan, lambda path: store.append(path, self.outcome("wrong", 1), request_key="wrong")):
            with self.assertRaises(IntegrityError):
                action(other)
        with patch.object(store.sqlite3, "connect", side_effect=sqlite3.OperationalError("disk unavailable")):
            with self.assertRaises(StoreError):
                store.initialize(self.root / "unavailable")

    def test_wal_inspection_is_refused_before_creating_sidecars(self):
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
        finally:
            connection.close()
        before = {p.name:p.read_bytes() for p in self.path.parent.iterdir()}
        self.assertEqual(set(before), {store.DATABASE_NAME})
        for action in (store.status, store.integrity_scan, store.migration_status, store.recover):
            with self.assertRaises(StoreError):
                action(self.path)
        self.assertEqual({p.name:p.read_bytes() for p in self.path.parent.iterdir()}, before)

    def test_backup_and_restore_only_create_new_private_directories(self):
        store.append(self.path, self.outcome("before", 1), request_key="before")
        backup = store.backup(self.path, self.root / "backup")
        store.append(self.path, self.outcome("after", -500), request_key="after")
        restored = store.restore(backup, self.root / "recovery")
        self.assertEqual(store.status(restored)["events"], 1)
        self.assertEqual(store.status(self.path)["events"], 2)
        with self.assertRaises(StoreError):
            store.restore(backup, self.path.parent)
        self.assertEqual(store.status(self.path)["cases"][0]["known_cash_delta_cents"], -499)

    def test_read_paths_never_create_files_or_migrate_and_future_writes_are_refused(self):
        missing = self.root / "missing" / store.DATABASE_NAME
        before = {str(p.relative_to(self.root)):p.read_bytes() if p.is_file() else None for p in self.root.rglob("*")}
        for action in (store.status, store.integrity_scan, store.migration_status, lambda path: store.get_record(path, "x")):
            with self.assertRaises(StoreError):
                action(missing)
        store.status(self.path)
        store.integrity_scan(self.path)
        self.assertEqual(store.migration_status(self.path)["steps"], [])
        after = {str(p.relative_to(self.root)):p.read_bytes() if p.is_file() else None for p in self.root.rglob("*")}
        self.assertEqual(after, before)
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("PRAGMA user_version=99")
        finally:
            connection.close()
        snapshot = self.path.read_bytes()
        self.assertFalse(store.migration_status(self.path)["supported"])
        with self.assertRaises(VersionError):
            store.status(self.path)
        with self.assertRaises(VersionError):
            store.append(self.path, self.outcome("new", 1), request_key="new")
        self.assertEqual(self.path.read_bytes(), snapshot)


if __name__ == "__main__":
    unittest.main()
