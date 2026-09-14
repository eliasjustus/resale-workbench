"""Frozen pre-operations behavior; all retained evidence here is invented.

These inputs/results come from the pinned baseline, not the current demo builder.
Do not regenerate expected results to accommodate an operational-layer change.
This file also runs directly against an installed wheel outside the checkout.
"""
from contextlib import closing, redirect_stderr, redirect_stdout
import copy
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from evaluation.manual import seal_review
from evaluation.service import evaluate_capture
from pilot import queue
from pilot.config import load_config
from pilot.status import snapshot
from resale_tool.cli import main


FIXTURE = Path(__file__).parent / 'fixtures' / 'operations-baseline.json'


def write_json(path, value):
    path.write_bytes((json.dumps(value, indent=2) + '\n').encode('utf-8'))


def inventory(root):
    return {p.relative_to(root).as_posix():
            ('file', hashlib.sha256(p.read_bytes()).hexdigest()) if p.is_file() else ('directory', None)
            for p in root.rglob('*')}


class OperationsCompatibilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))

    def case(self, name):
        case = copy.deepcopy(self.fixture['cases'][name])
        capture = self.root / name
        capture.mkdir()
        for relative, content in self.fixture['retained_files'].items():
            path = capture / relative
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(content.encode('utf-8'))
        write_json(capture / 'record.json', case['record'])
        review = case['review']
        review['source_capture'] = '.'
        review['source_record_sha256'] = hashlib.sha256((capture / 'record.json').read_bytes()).hexdigest()
        review_path = capture / 'review.json'
        write_json(review_path, review)
        if name != 'legacy':
            sealed = copy.deepcopy(review)
            sealed['retained_evidence'] = case['retained_evidence']
            sealed['retained_evidence']['root'] = str(capture)
            review_path = capture / 'sealed.json'
            write_json(review_path, sealed)
        return capture, review_path, case['expected']

    def test_current_sealer_matches_frozen_retained_evidence_contract(self):
        capture, sealed, _ = self.case('supported')
        before = inventory(capture)
        self.assertEqual(seal_review(capture, capture / 'review.json'),
                         json.loads(sealed.read_text(encoding='utf-8')))
        self.assertEqual(inventory(capture), before)

    def cli(self, args):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                return main(args)
            except SystemExit as exc:
                return exc.code

    def restore_paths(self, value):
        if isinstance(value, dict):
            return {k: self.restore_paths(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.restore_paths(v) for v in value]
        if isinstance(value, str) and value.startswith('${ROOT}'):
            # Only the temporary workspace prefix/separators vary by host.
            relative = value[len('${ROOT}'):].replace('\\', '/').lstrip('/')
            return str(self.root.joinpath(*relative.split('/')))
        return value

    def test_current_results_and_cli_exit_codes_match_frozen_baseline(self):
        for name in ('supported', 'unsupported', 'unresolved'):
            with self.subTest(name=name):
                capture, review, expected = self.case(name)
                before = inventory(capture)
                self.assertEqual(evaluate_capture(capture, review), expected)
                output = self.root / (name + '-result.json')
                self.assertEqual(self.cli(['evaluate', str(capture), str(review), '--output', str(output)]), 0)
                self.assertEqual(json.loads(output.read_text()), expected)
                self.assertEqual(inventory(capture), before)
                self.assertFalse(expected['purchase_authorized'])

    def test_retained_evidence_requirement_cannot_be_bypassed(self):
        capture, review, _ = self.case('supported')
        with self.assertRaisesRegex(ValueError, 'seal-review'):
            evaluate_capture(capture, capture / 'review.json')
        for relative, message in [('synthetic-evidence.txt', 'file hash changed'),
                                  ('photos/01.txt', 'gallery path/hash mismatch')]:
            with self.subTest(relative=relative):
                path = capture / relative
                original = path.read_bytes()
                path.write_bytes(b'SYNTHETIC changed evidence')
                try:
                    with self.assertRaisesRegex(ValueError, message):
                        evaluate_capture(capture, review)
                    output = self.root / 'rejected.json'
                    self.assertEqual(self.cli(['evaluate', str(capture), str(review), '--output', str(output)]), 2)
                    self.assertFalse(output.exists())
                finally:
                    path.write_bytes(original)

    def test_legacy_replay_is_explicit_and_matches_frozen_historical_result(self):
        capture, review, expected = self.case('legacy')
        before = inventory(capture)
        with self.assertRaisesRegex(ValueError, 'Current evaluation requires schema 2'):
            evaluate_capture(capture, review)
        output = self.root / 'legacy-result.json'
        args = ['evaluate', str(capture), str(review), '--output', str(output)]
        self.assertEqual(self.cli(args), 2)
        self.assertFalse(output.exists())
        self.assertEqual(evaluate_capture(capture, review, legacy_replay=True), expected)
        self.assertEqual(self.cli([*args, '--legacy-replay']), 0)
        self.assertEqual(json.loads(output.read_text()), expected)
        self.assertEqual(inventory(capture), before)
        self.assertTrue(expected['replay_only'])
        self.assertFalse(expected['purchase_authorized'])
        self.assertNotIn('readiness', expected)

    def test_old_config_and_run_inspection_preserve_all_stored_bytes(self):
        config = self.root / 'resale.toml'
        config.write_bytes(self.fixture['config_text'].encode('utf-8'))
        run = self.root / 'frozen-run'
        (run / 'playbooks').mkdir(parents=True)
        for name, content in self.fixture['frozen_playbooks'].items():
            (run / 'playbooks' / name).write_bytes(content.encode('utf-8'))
        manifest = self.restore_paths(self.fixture['run_manifest'])
        write_json(run / 'manifest.json', manifest)
        state = self.root / 'private-data/pilot-state.sqlite3'
        state.parent.mkdir()
        write_json(run / 'pilot.json', {'state': str(state)})
        # Materialize the old schema directly: a future queue initializer cannot
        # silently update the test's supposedly historical run.
        with closing(sqlite3.connect(state)) as db:
            with db:
                for statement in self.fixture['queue_schema']:
                    db.execute(statement)
                db.execute('INSERT INTO runs(run,manifest) VALUES(?,?)', (str(run), json.dumps(manifest)))
        before = inventory(self.root)
        self.assertEqual(load_config(config), self.restore_paths(self.fixture['configuration']))
        with patch.object(queue, 'now', return_value='2026-09-12T12:10:00+00:00'):
            self.assertEqual(snapshot(run), self.restore_paths(self.fixture['run_status']))
            self.assertEqual(self.cli(['run', 'status', str(run)]), 0)
        self.assertEqual(inventory(self.root), before)

    def test_frozen_policy_equality_and_conflict_preserve_review(self):
        capture, review, expected = self.case('supported')
        config = self.root / 'resale.toml'
        text = self.fixture['config_text']
        config.write_text(text.replace('minimum_profit_eur = "20"', 'minimum_profit_eur = "20.00"'), encoding='utf-8')
        before = inventory(capture)
        self.assertEqual(evaluate_capture(capture, review, config_path=config), expected)
        config.write_text(text.replace('minimum_profit_eur = "20"', 'minimum_profit_eur = "55"'), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'differs'):
            evaluate_capture(capture, review, config_path=config)
        self.assertEqual(inventory(capture), before)


if __name__ == '__main__':
    unittest.main()
