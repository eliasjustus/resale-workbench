import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools.check_candidate import check_candidate


class CandidateCheckTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'source'
        self.root.mkdir()
        self.destination = self.root.parent / 'candidate'
        (self.root / 'module.py').write_text('VALUE = 1\n', encoding='utf-8')
        (self.root / 'release-files.json').write_text(json.dumps(
            {'schema_version': 1, 'files': ['module.py', 'release-files.json']}), encoding='utf-8')

    def process(self, command, cwd, stdout, **kwargs):
        stdout.write('Synthetic command output\n')
        if command[2] == 'build':
            (cwd / 'dist').mkdir()
            (cwd / 'dist' / 'example.whl').write_bytes(b'synthetic wheel fixture')
            (cwd / 'dist' / 'example.tar.gz').write_bytes(b'synthetic source fixture')
            (cwd / 'unlisted-private.txt').write_text('must not be archived')
        return subprocess.CompletedProcess(command, 0)

    def test_build_then_full_suite_and_exact_zip_inputs(self):
        with patch('tools.check_candidate.subprocess.run', side_effect=self.process) as run:
            report = check_candidate(self.root, self.destination, python='selected-python')
        self.assertEqual([call.args[0] for call in run.call_args_list], [
            ['selected-python', '-m', 'build', '--no-isolation'],
            ['selected-python', '-m', 'unittest', 'discover', '-s', 'tests', '-v']])
        self.assertEqual(report['status'], 'passed')
        self.assertFalse(report['published'])
        with zipfile.ZipFile(self.destination / 'source.zip') as archive:
            self.assertEqual(set(archive.namelist()), {'module.py', 'release-files.json', 'SOURCE-MANIFEST.json'})
            self.assertEqual(archive.read('module.py'), (self.root / 'module.py').read_bytes())
        self.assertIn('Synthetic command output', (self.destination / 'verification' / 'tests.log').read_text())

    def test_failed_step_stops_without_success_archive(self):
        for failed in ('build', 'unittest'):
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as temp:
                destination = Path(temp) / 'candidate'
                def process(command, **kwargs):
                    if command[2] == failed:
                        return subprocess.CompletedProcess(command, 7)
                    return self.process(command, **kwargs)
                with patch('tools.check_candidate.subprocess.run', side_effect=process) as run:
                    with self.assertRaisesRegex(ValueError, 'exit code 7'):
                        check_candidate(self.root, destination)
                self.assertEqual(run.call_count, 1 if failed == 'build' else 2)
                self.assertFalse((destination / 'source.zip').exists())
                report = json.loads((destination / 'verification' / 'report.json').read_text())
                self.assertEqual(report['status'], 'failed')

    def test_source_or_manifest_tamper_rejected_before_tests(self):
        for name in ('module.py', 'SOURCE-MANIFEST.json'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                destination = Path(temp) / 'candidate'
                def process(command, **kwargs):
                    result = self.process(command, **kwargs)
                    path = kwargs['cwd'] / name
                    path.write_bytes(path.read_bytes() + b'\n')
                    return result
                with patch('tools.check_candidate.subprocess.run', side_effect=process) as run:
                    with self.assertRaisesRegex(ValueError, 'changed during verification'):
                        check_candidate(self.root, destination)
                self.assertEqual(run.call_count, 1)
                self.assertFalse((destination / 'source.zip').exists())

    def test_existing_destination_is_preserved_and_no_commands_start(self):
        self.destination.mkdir()
        (self.destination / 'keep').write_text('original')
        with patch('tools.check_candidate.subprocess.run') as run:
            with self.assertRaises(FileExistsError):
                check_candidate(self.root, self.destination)
        run.assert_not_called()
        self.assertEqual((self.destination / 'keep').read_text(), 'original')

    def test_missing_build_artifacts_cannot_silently_skip_distribution_checks(self):
        with patch('tools.check_candidate.subprocess.run', return_value=subprocess.CompletedProcess([], 0)) as run:
            with self.assertRaisesRegex(ValueError, 'exactly one wheel'):
                check_candidate(self.root, self.destination)
        self.assertEqual(run.call_count, 1)
        self.assertFalse((self.destination / 'source.zip').exists())

    def test_test_step_source_edit_is_rejected_after_successful_exit(self):
        def process(command, **kwargs):
            result = self.process(command, **kwargs)
            if command[2] == 'unittest':
                (kwargs['cwd'] / 'module.py').write_text('changed by test process')
            return result
        with patch('tools.check_candidate.subprocess.run', side_effect=process) as run:
            with self.assertRaisesRegex(ValueError, 'changed during verification'):
                check_candidate(self.root, self.destination)
        self.assertEqual(run.call_count, 2)
        self.assertFalse((self.destination / 'source.zip').exists())
        report = json.loads((self.destination / 'verification' / 'report.json').read_text())
        self.assertEqual(report['status'], 'failed')

    def test_private_patterns_still_reject_before_any_commands_or_candidate(self):
        with patch('tools.check_candidate.subprocess.run') as run:
            with self.assertRaisesRegex(ValueError, 'local_private_marker'):
                check_candidate(self.root, self.destination, private_patterns=['VALUE'])
        run.assert_not_called()
        self.assertFalse(self.destination.exists())


if __name__ == '__main__':
    unittest.main()
