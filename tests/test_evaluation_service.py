"""Both CLI aliases must enforce the same retained-evidence and policy boundary."""
from contextlib import redirect_stderr, redirect_stdout
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest

from evaluation.__main__ import main as evaluation_main
from evaluation.manual import seal_review
from evaluation.service import evaluate_capture
from pilot.config import economic_policy, example_config
from resale_tool.cli import main as resale_main
from resale_tool.demo import create_demo


class EvaluationServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        create_demo(self.root / 'demo')
        self.capture = self.root / 'demo/supported-example'
        self.review = self.capture / 'review.json'
        self.config = self.root / 'settings.toml'
        self.config.write_text(example_config(), encoding='utf-8')

    def call_alias(self, name, output, *extra):
        if name == 'resale':
            command = ['evaluate', str(self.capture), str(self.review), '--output', str(output), *extra]
            entry = resale_main
        else:
            command = ['check', str(self.review), '--output', str(output), *extra]
            entry = evaluation_main
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return entry(command)

    def assert_aliases_reject(self, expected):
        for name in ('resale', 'evaluation'):
            with self.subTest(alias=name):
                output = self.root / (name + '-rejected.json')
                with self.assertRaises((ValueError, SystemExit)) as caught:
                    self.call_alias(name, output)
                if isinstance(caught.exception, ValueError):
                    self.assertIn(expected, str(caught.exception))
                self.assertFalse(output.exists())

    def test_both_aliases_produce_identical_current_results(self):
        results = []
        for name in ('resale', 'evaluation'):
            output = self.root / (name + '-result.json')
            self.call_alias(name, output)
            results.append(json.loads(output.read_text(encoding='utf-8')))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0]['outcome'], 'supported')
        self.assertTrue(results[0]['synthetic'])

    def test_missing_synthetic_source_cannot_bypass_verification_via_old_alias(self):
        (self.capture / 'synthetic-evidence.txt').unlink()
        self.assert_aliases_reject('Retained evidence file is missing')

    def test_changed_sealed_evidence_rejected_by_both_aliases(self):
        sealed = seal_review(self.capture, self.review)
        self.review.write_text(json.dumps(sealed), encoding='utf-8')
        (self.capture / 'synthetic-evidence.txt').write_text('Changed after evidence review', encoding='utf-8')
        self.assert_aliases_reject('Retained evidence file hash changed')

    def test_changed_capture_rejected_by_both_aliases(self):
        record_path = self.capture / 'record.json'
        record = json.loads(record_path.read_text(encoding='utf-8'))
        record['capture_id'] = 'changed-source'
        record_path.write_text(json.dumps(record), encoding='utf-8')
        self.assert_aliases_reject('Review source hash does not match')

    def test_frozen_policy_matches_equivalent_amounts_and_rejects_changed_policy(self):
        review = json.loads(self.review.read_text(encoding='utf-8'))
        review['economic_policy'] = economic_policy({'minimum_profit_eur': '20.00'})
        self.review.write_text(json.dumps(review), encoding='utf-8')
        for name in ('resale', 'evaluation'):
            output = self.root / (name + '-policy.json')
            self.call_alias(name, output, '--config', str(self.config))
            self.assertEqual(json.loads(output.read_text())['outcome'], 'supported')
        self.config.write_text(example_config().replace('minimum_profit_eur = "20"',
                                                       'minimum_profit_eur = "55"'), encoding='utf-8')
        for name in ('resale', 'evaluation'):
            output = self.root / (name + '-changed-policy.json')
            with self.assertRaises((ValueError, SystemExit)):
                self.call_alias(name, output, '--config', str(self.config))
            self.assertFalse(output.exists())

    def make_legacy_review(self):
        review = json.loads(self.review.read_text(encoding='utf-8'))
        review['schema_version'] = 1
        review['source_capture'] = str(self.capture)
        review['comps'] = [dict(copy.deepcopy(review['comps'][0]), sale_id=str(i)) for i in range(5)]
        self.review.write_text(json.dumps(review), encoding='utf-8')

    def test_legacy_schema_needs_explicit_replay_and_output_is_marked(self):
        self.make_legacy_review()
        self.assert_aliases_reject('Current evaluation requires schema 2')
        # A replay deliberately does not claim current retained-file verification.
        (self.capture / 'synthetic-evidence.txt').unlink()
        results = []
        for name in ('resale', 'evaluation'):
            output = self.root / (name + '-legacy-replay.json')
            self.call_alias(name, output, '--legacy-replay')
            results.append(json.loads(output.read_text(encoding='utf-8')))
        self.assertEqual(results[0], results[1])
        result = results[0]
        self.assertEqual(result['outcome'], 'supported')
        self.assertTrue(result['replay_only'])
        self.assertEqual(result['validation_scope'], 'legacy_record_hash_and_declarations_only')
        self.assertFalse(result['purchase_authorized'])

    def test_legacy_switch_cannot_bypass_current_evidence_or_change_policy(self):
        with self.assertRaisesRegex(ValueError, 'restricted to schema-1'):
            evaluate_capture(self.capture, self.review, legacy_replay=True)
        self.make_legacy_review()
        with self.assertRaisesRegex(ValueError, 'historical default policy'):
            evaluate_capture(self.capture, self.review, self.config, legacy_replay=True)

    def test_legacy_replay_honors_policy_already_frozen_in_review(self):
        self.make_legacy_review()
        review = json.loads(self.review.read_text(encoding='utf-8'))
        review['economic_policy'] = economic_policy({'minimum_profit_eur': '55'})
        self.review.write_text(json.dumps(review), encoding='utf-8')
        result = evaluate_capture(self.capture, self.review, legacy_replay=True)
        self.assertEqual(result['outcome'], 'unsupported')
        self.assertEqual(result['arithmetic']['margin_eur'], '40.00')
        self.assertEqual(result['arithmetic']['required_profit_eur'], '55.00')
        self.assertEqual(result['economic_policy']['minimum_profit_eur'], '55')
        self.assertTrue(result['replay_only'])


if __name__ == '__main__':
    unittest.main()
