"""No invented historical operations migration; current version is explicit."""
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from operations import store
from operations.errors import VersionError


class OperationsMigrationTests(unittest.TestCase):
    def test_future_version_hot_journal_is_preserved_before_refusal(self):
        with tempfile.TemporaryDirectory() as temp:
            path = store.initialize(Path(temp) / "future")
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA user_version=99")
                connection.execute("CREATE TABLE future_data (content TEXT)")
                connection.executemany("INSERT INTO future_data VALUES (?)", [("original"*1000,)]*100)
                connection.commit()
            finally:
                connection.close()
            script = "import os,sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('PRAGMA cache_size=1'); c.execute('BEGIN IMMEDIATE'); c.execute(\"UPDATE future_data SET content='changed'\"); os._exit(73)"
            child = subprocess.run([sys.executable,"-c",script,str(path)], capture_output=True)
            self.assertEqual(child.returncode, 73)
            before = {p.name:p.read_bytes() for p in path.parent.iterdir()}
            self.assertIn(store.DATABASE_NAME+"-journal", before)
            for action in (store.status, store.rebuild_projections, store.recover):
                with self.assertRaises(VersionError):
                    action(path)
            self.assertEqual({p.name:p.read_bytes() for p in path.parent.iterdir()}, before)

    def test_unversioned_database_is_not_adopted_by_inspection(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pilot-state.sqlite3"
            connection = sqlite3.connect(path)
            try:
                connection.execute("CREATE TABLE old_queue (value TEXT)")
                connection.execute("INSERT INTO old_queue VALUES ('preserved')")
                connection.commit()
            finally:
                connection.close()
            before = path.read_bytes()
            self.assertEqual(store.migration_status(path), {"current_version":0,"target_version":1,"supported":False,"steps":[],"writes":False})
            with self.assertRaises(VersionError):
                store.status(path)
            with self.assertRaises(VersionError):
                store.rebuild_projections(path)
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
