import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from pilot.support import summary


class SupportTests(unittest.TestCase):
    def test_only_fixed_fields_and_counts_escape_private_records(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder).resolve()
            state = run / 'state.sqlite3'
            secret = 'PRIVATE SENTINEL'
            db = sqlite3.connect(state)
            db.executescript('CREATE TABLE runs(run TEXT, manifest TEXT); '
                             'CREATE TABLE cases(run TEXT, stage TEXT, data TEXT); '
                             'CREATE TABLE sightings(run TEXT);')
            db.execute('INSERT INTO runs VALUES (?,?)', (str(run), json.dumps({'workflow_version': 2, 'center': secret})))
            db.execute('INSERT INTO cases VALUES (?,?,?)', (str(run), secret, json.dumps({'outcome': secret, 'prompt': secret})))
            db.execute('INSERT INTO sightings VALUES (?)', (str(run),))
            db.commit()
            db.close()
            (run / 'pilot.json').write_text(json.dumps({'state': str(state)}), encoding='utf-8')
            before = state.read_bytes()
            result = summary(run)
            self.assertNotIn(secret, json.dumps(result))
            self.assertNotIn(str(run), json.dumps(result))
            self.assertEqual(result['counts']['stages'], {'unknown': 1})
            self.assertEqual(result['counts']['outcomes'], {'not_recorded': 1})
            self.assertEqual(before, state.read_bytes())


if __name__ == '__main__':
    unittest.main()
