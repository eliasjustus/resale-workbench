"""Real local CLI workflows, using fictional rows and temporary state only."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from pilot import queue
from pilot.config import example_config
from pilot.reporting import render_report


class PilotCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / 'run'
        queue.init(self.run, state=self.root / 'state.sqlite3', start='2026-01-01T12:00:00Z')

    def invoke(self, args):
        output = io.StringIO()
        with redirect_stdout(output):
            code = queue.main(args, prog='resale run')
        return code, json.loads(output.getvalue())

    def discovery(self, directory):
        queue.write(directory / 'rows.json', [{'listing_id': '123', 'text': 'Heute, 11:30',
                    'observed_at': '2026-01-01T11:00:00Z'}])
        queue.write(directory / 'summary.json', {'row_occurrences': 1, 'coverage_complete': False,
                                                 'stop_reason': 'synthetic test'})

    def test_default_discovery_directory_import_and_explicit_pair_resume(self):
        directory = self.run / 'discovery'
        self.discovery(directory)
        code, result = self.invoke(['import-discovery', str(self.run)])
        self.assertEqual(code, 0)
        self.assertFalse(result['already_imported'])
        code, result = self.invoke(['import-discovery', str(self.run), '--rows', str(directory / 'rows.json'),
                                   '--summary', str(directory / 'summary.json')])
        self.assertEqual(code, 0)
        self.assertTrue(result['already_imported'])
        self.assertEqual(queue.report(self.run)['row_occurrences'], 1)

    def test_custom_discovery_directory_and_missing_default_fail_closed(self):
        code, result = self.invoke(['import-discovery', str(self.run)])
        self.assertEqual(code, 1)
        self.assertIn('error', result)
        directory = self.root / 'retained-search'
        self.discovery(directory)
        code, result = self.invoke(['import-discovery', str(self.run), '--discovery-dir', str(directory)])
        self.assertEqual(code, 0)
        self.assertEqual(result['rows'], 1)

    def test_incomplete_or_ambiguous_discovery_modes_do_not_import(self):
        directory = self.run / 'discovery'
        self.discovery(directory)
        modes = [['--rows', str(directory / 'rows.json')],
                 ['--summary', str(directory / 'summary.json')],
                 ['--discovery-dir', str(directory), '--rows', str(directory / 'rows.json'),
                  '--summary', str(directory / 'summary.json')]]
        for mode in modes:
            with self.subTest(mode=mode):
                code, result = self.invoke(['import-discovery', str(self.run), *mode])
                self.assertEqual(code, 1)
                self.assertIn('error', result)
        self.assertEqual(queue.report(self.run)['row_occurrences'], 0)

    def test_compact_and_full_reports_preserve_the_existing_output_contract(self):
        self.discovery(self.run / 'discovery')
        self.invoke(['import-discovery', str(self.run)])
        code, compact = self.invoke(['report', str(self.run)])
        self.assertEqual(code, 0)
        self.assertNotIn('manifest', compact)
        self.assertEqual(compact['row_occurrences'], 1)
        code, full = self.invoke(['report', str(self.run), '--full'])
        self.assertEqual(code, 0)
        self.assertEqual(full['manifest']['run_id'], self.run.name)
        self.assertEqual(Path(compact['readable_report_path']).read_text(encoding='utf-8'), render_report(full))
        self.assertFalse(full['purchase_authorized'])

    def test_config_selects_default_run_beside_config_and_single_category(self):
        workspace = self.root / 'workspace'
        workspace.mkdir()
        config = workspace / 'resale.toml'
        config.write_text(example_config(), encoding='utf-8')
        code, result = self.invoke(['init', '--config', str(config)])
        self.assertEqual(code, 0)
        run = Path(result['run_path'])
        self.assertEqual(run.parent, workspace / 'runs')
        self.assertEqual(result['sampled_category'], 'computers')
        self.assertEqual(queue.read(run / 'pilot.json')['state'], str(workspace / 'private-data/pilot-state.sqlite3'))
        self.assertNotIn('run_path', queue.read(run / 'manifest.json'))

    def test_ambiguous_config_category_and_missing_workspace_are_rejected_before_writes(self):
        config = self.root / 'resale.toml'
        config.write_text(example_config().replace('["computers"]', '["computers", "other"]'), encoding='utf-8')
        code, result = self.invoke(['init', '--config', str(config)])
        self.assertEqual(code, 1)
        self.assertIn('--category', result['error'])
        self.assertFalse((self.root / 'runs').exists())
        code, result = self.invoke(['init'])
        self.assertEqual(code, 1)
        self.assertIn('--config', result['error'])
        explicit = self.root / 'explicit'
        code, result = self.invoke(['init', str(explicit), '--config', str(config), '--category', 'other'])
        self.assertEqual(code, 0)
        self.assertEqual(result['run_path'], str(explicit))


if __name__ == '__main__':
    unittest.main()
