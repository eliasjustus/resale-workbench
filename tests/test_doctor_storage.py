"""Doctor must distinguish local usability from unchecked external availability."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pilot.config import example_config
from resale_tool.cli import init_workspace, main, storage_readiness


class DoctorStorageTests(unittest.TestCase):
    def doctor(self, config):
        with contextlib.redirect_stdout(io.StringIO()) as output, patch(
                'socket.socket', side_effect=AssertionError('doctor must remain offline')):
            status = main(['doctor', '--config', str(config)])
        return status, json.loads(output.getvalue())

    def config(self, root, data_dir):
        config = root / 'resale.toml'
        config.write_text(example_config().replace('data_dir = "./private-data"',
                                                   f'data_dir = "{data_dir}"'), encoding='utf-8')
        return config

    def test_existing_file_and_ancestor_file_fail_doctor(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            occupied = root / 'occupied'
            occupied.write_text('preserve original')
            for path in ('./occupied', './occupied/child'):
                with self.subTest(path=path):
                    status, result = self.doctor(self.config(root, path))
                    self.assertEqual(status, 1)
                    self.assertFalse(result['offline_ready'])
                    self.assertFalse(result['checks']['storage_writable'])
                    self.assertIn('NotADirectoryError', result['storage']['error'])
            self.assertEqual(occupied.read_text(), 'preserve original')

    def test_write_probe_cleans_only_new_empty_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            existing = root / 'keep'
            existing.mkdir()
            (existing / 'original.txt').write_text('unchanged')
            result = storage_readiness(existing / 'new' / 'nested')
            self.assertTrue(result['usable'])
            self.assertEqual(list(existing.iterdir()), [existing / 'original.txt'])
            self.assertTrue(storage_readiness(existing)['usable'])
            self.assertEqual((existing / 'original.txt').read_text(), 'unchanged')

    def test_permission_failure_is_readiness_failure_and_cleans_probe_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self.config(root, './private-data/nested')
            with patch('resale_tool.cli.tempfile.TemporaryFile', side_effect=PermissionError('permission denied')):
                status, result = self.doctor(config)
            self.assertEqual(status, 1)
            self.assertFalse(result['offline_ready'])
            self.assertIn('PermissionError', result['storage']['error'])
            self.assertFalse((root / 'private-data').exists())

    def test_readiness_does_not_claim_external_model_or_browser_availability(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self.config(root, './private-data')
            config.write_text(config.read_text().replace('gpt-5.6-luna', 'nonexistent-provider/model'))
            status, result = self.doctor(config)
            self.assertEqual(status, 0)
            self.assertTrue(result['model_identifier_syntax_checked'])
            for key in ('model_availability_checked', 'model_effort_compatibility_checked',
                        'browser_binaries_checked', 'source_access_checked', 'network_used'):
                self.assertFalse(result[key], key)
            self.assertFalse((root / 'private-data').exists())

    def test_workspace_readme_explains_paths_and_points_to_help_and_guides(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'workspace'
            init_workspace(root)
            readme = (root / 'README.txt').read_text(encoding='utf-8')
            for fragment in ('resale --help', 'README.md', 'RUNNER.md', 'PRIVACY.md',
                             'relative RUN/output paths', './runs/first-review'):
                self.assertIn(fragment, readme)


if __name__ == '__main__':
    unittest.main()
