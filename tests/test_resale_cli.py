import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from resale_tool.cli import draft_review, evaluate_capture, init_workspace, main
from resale_tool.demo import create_demo, write_json


class ResaleCliTests(unittest.TestCase):
    def test_demo_runs_real_gates_offline_and_preserves_originals(self):
        with tempfile.TemporaryDirectory() as temp, patch('socket.socket', side_effect=AssertionError('network forbidden')):
            root = Path(temp) / 'demo'
            page = create_demo(root)
            report = json.loads((root / 'summary.json').read_text())
            self.assertEqual([r['outcome'] for r in report['cases']], ['supported', 'unsupported', 'unresolved'])
            self.assertTrue(all(r['synthetic'] and not r['purchase_authorized'] for r in report['cases']))
            self.assertIn('SYNTHETIC DEMO', page.read_text(encoding='utf-8'))
            self.assertNotIn('https://', page.read_text(encoding='utf-8'))
            self.assertIsNone(report['cases'][2]['arithmetic'])
            self.assertEqual(report['cases'][0]['arithmetic']['margin_eur'], '40.00')
            before = page.read_bytes()
            with self.assertRaises(FileExistsError):
                create_demo(root)
            self.assertEqual(page.read_bytes(), before)

    def test_manual_draft_abstains_and_hash_substitution_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_demo(root / 'demo')
            capture = root / 'demo' / 'supported-example'
            draft = draft_review(capture)
            write_json(root / 'manual.json', draft)
            self.assertEqual(evaluate_capture(capture, root / 'manual.json')['outcome'], 'unresolved')
            complete = evaluate_capture(capture, capture / 'review.json')
            self.assertEqual(complete['outcome'], 'supported')
            self.assertTrue(complete['synthetic'])
            record = capture / 'record.json'
            record.write_bytes(record.read_bytes() + b'\n')
            with self.assertRaisesRegex(ValueError, 'source hash'):
                evaluate_capture(capture, capture / 'review.json')

    def test_cli_errors_do_not_replace_results(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_demo(root / 'demo')
            capture = root / 'demo' / 'supported-example'
            destination = root / 'result.json'
            destination.write_text('preserve me')
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                main(['evaluate', str(capture), str(capture / 'review.json'), '--output', str(destination)])
            self.assertEqual(raised.exception.code, 2)
            self.assertEqual(destination.read_text(), 'preserve me')

    def test_workspace_scaffolding_is_private_and_new_only(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / 'workspace'
            config = init_workspace(folder)
            self.assertEqual(config.name, 'resale.toml')
            self.assertIn('[search]', config.read_text(encoding='utf-8'))
            self.assertEqual((folder / '.gitignore').read_text(), '*\n')
            from pilot.config import load_config
            self.assertEqual(Path(load_config(config)['data_dir']), (folder / 'private-data').resolve())
            with self.assertRaises(FileExistsError):
                init_workspace(folder)

    def test_manual_review_freezes_economic_policy(self):
        from pilot.config import example_config
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_demo(root / 'demo')
            capture = root / 'demo' / 'supported-example'
            config = root / 'resale.toml'
            config.write_text(example_config().replace('minimum_profit_eur = "20"', 'minimum_profit_eur = "50"'), encoding='utf-8')
            draft = draft_review(capture, config)
            self.assertEqual(draft['economic_policy']['minimum_profit_eur'], '50')
            complete = json.loads((capture / 'review.json').read_text(encoding='utf-8'))
            complete['economic_policy'] = draft['economic_policy']
            write_json(root / 'manual.json', complete)
            self.assertEqual(evaluate_capture(capture, root / 'manual.json')['outcome'], 'unsupported')
            config.write_text(example_config(), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'differs'):
                evaluate_capture(capture, root / 'manual.json', config)

    def test_doctor_is_offline(self):
        with contextlib.redirect_stdout(io.StringIO()) as output, patch('socket.socket', side_effect=AssertionError('network forbidden')):
            self.assertEqual(main(['doctor']), 0)
        self.assertTrue(json.loads(output.getvalue())['offline_ready'])


if __name__ == '__main__':
    unittest.main()
