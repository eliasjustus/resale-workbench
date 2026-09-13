"""Run after python -m build to verify archives and installed CLI off-checkout."""
import json
from email.parser import Parser
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]


class DistributionTests(unittest.TestCase):
    def artifacts(self):
        project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']
        prefix = project['name'].replace('-', '_') + '-' + project['version']
        wheels = sorted((ROOT / 'dist').glob(prefix + '-*.whl'))
        sources = sorted((ROOT / 'dist').glob(prefix + '.tar.gz'))
        if not wheels or not sources:
            self.skipTest('Build current-version artifacts first: python -m build')
        return wheels[-1], sources[-1]

    def test_archives_only_contain_explicit_source_packages_and_metadata(self):
        wheel, source = self.artifacts()
        package_roots = {'collector', 'evaluation', 'pilot', 'resale_tool'}
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
        for name in names:
            first = PurePosixPath(name).parts[0]
            self.assertTrue(first in package_roots or first.endswith('.dist-info'), name)
            self.assertFalse(name.endswith(('.db', '.sqlite3', '.pyc')), name)
        self.assertIn('resale_tool/resources/REVIEWER.md', names)
        self.assertIn('evaluation/check_run_handoffs.py', names)
        with tarfile.open(source) as archive:
            source_names = [PurePosixPath(item.name).parts[1:] for item in archive.getmembers() if item.isfile()]
        permitted = package_roots | {'tests', 'tools', 'release-files.json', '.github', 'pyproject.toml', 'MANIFEST.in',
                                      'README.md', 'requirements.txt', 'PKG-INFO', 'setup.cfg',
                                      'AGENTS.md', 'CLAUDE.md', 'PROJECT.md', 'DAILY.md', 'RUNNER.md',
                                      'SPECIALISTS.md', 'REVIEWER.md', 'VALUATOR.md', 'PRIVACY.md',
                                      'PUBLISHING.md', 'CONTRIBUTING.md', 'LICENSE', 'SECURITY.md',
                                      'CHANGELOG.md', '.gitignore'}
        for parts in source_names:
            self.assertTrue(parts[0] in permitted or parts[0].endswith('.egg-info'), '/'.join(parts))
            self.assertNotIn('__pycache__', parts)

    def test_release_identity_and_mit_license_are_in_both_archives(self):
        wheel, source = self.artifacts()
        project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']
        license_bytes = (ROOT / 'LICENSE').read_bytes()
        self.assertIn(b'MIT License', license_bytes)
        self.assertIn(b'Permission is hereby granted, free of charge', license_bytes)
        with zipfile.ZipFile(wheel) as archive:
            metadata_paths = [name for name in archive.namelist() if name.endswith('.dist-info/METADATA')]
            self.assertEqual(len(metadata_paths), 1)
            metadata = archive.read(metadata_paths[0]).decode('utf-8')
            license_path = metadata_paths[0].rsplit('/', 1)[0] + '/licenses/LICENSE'
            self.assertEqual(archive.read(license_path), license_bytes)
        with tarfile.open(source) as archive:
            members = {PurePosixPath(member.name).parts[1:]: member for member in archive.getmembers() if member.isfile()}
            self.assertEqual(archive.extractfile(members[('LICENSE',)]).read(), license_bytes)
            source_metadata = archive.extractfile(members[('PKG-INFO',)]).read().decode('utf-8')
        for raw in (metadata, source_metadata):
            fields = Parser().parsestr(raw)
            self.assertEqual(fields['Name'], 'resale-workbench')
            self.assertEqual(fields['Version'], project['version'])
            self.assertEqual(fields['Version'], '0.1.0')
            self.assertEqual(fields['License-Expression'], 'MIT')
            self.assertIn('LICENSE', fields.get_all('License-File', []))
            self.assertIn('Repository, https://github.com/eliasjustus/resale-workbench', fields.get_all('Project-URL', []))
            self.assertFalse(any('Private' in value or 'Pre-Alpha' in value
                                 for value in fields.get_all('Classifier', [])))

    def test_installed_wheel_runs_outside_checkout(self):
        wheel, _ = self.artifacts()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            installed = root / 'installed'
            result = subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-deps', '--no-compile',
                                     '--disable-pip-version-check', '--target', str(installed), str(wheel)],
                                    capture_output=True, text=True, cwd=root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            env = dict(os.environ, PYTHONPATH=str(installed), PYTHONNOUSERSITE='1')
            scripts = list(installed.rglob('resale.exe' if os.name == 'nt' else 'resale'))
            self.assertEqual(len(scripts), 1, 'Installed console entry point is missing or ambiguous')
            entry = subprocess.run([str(scripts[0]), '--version'], cwd=root, env=env, capture_output=True, text=True)
            self.assertEqual(entry.returncode, 0, entry.stdout + entry.stderr)
            with zipfile.ZipFile(wheel) as archive:
                metadata_path = next(name for name in archive.namelist() if name.endswith('.dist-info/METADATA'))
                expected_version = Parser().parsestr(archive.read(metadata_path).decode('utf-8'))['Version']
            self.assertEqual(entry.stdout.strip(), expected_version)
            def run(*args):
                result = subprocess.run([sys.executable, *args], cwd=root, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result.stdout
            # Location assertions ensure checkout modules cannot mask a missing wheel file.
            location = run('-c', 'import resale_tool, pilot.queue; print(resale_tool.__file__); print(pilot.queue.__file__)')
            self.assertTrue(all(str(installed) in line for line in location.strip().splitlines()))
            for module in ('resale_tool', 'pilot', 'evaluation', 'evaluation.check_run_handoffs', 'collector', 'collector.discover'):
                run('-m', module, '--help')
            run('-c', '''
import builtins
original = builtins.__import__
def without_browser(name, *args, **kwargs):
    if name.startswith('playwright'):
        raise ImportError('simulated optional dependency absent')
    return original(name, *args, **kwargs)
builtins.__import__ = without_browser
import collector.discover, collector.__main__, pilot.queue
from collector.browser import sync_playwright
try:
    sync_playwright()
except SystemExit as exc:
    assert 'optional dependency' in str(exc)
else:
    raise AssertionError('missing browser must give actionable error')
''')
            run('-m', 'resale_tool', 'demo', '--output', str(root / 'demo'))
            run('-m', 'resale_tool', 'init', str(root / 'workspace'))
            initialized = json.loads(run('-m', 'resale_tool', 'run', 'init', '--config', str(root / 'workspace/resale.toml')))
            run('-c', '''
from importlib import resources
from pathlib import Path
import sys
run = Path(sys.argv[1])
for role in ('REVIEWER.md', 'VALUATOR.md'):
    assert (run / 'playbooks' / role).read_bytes() == resources.files('resale_tool').joinpath('resources', role).read_bytes()
''', initialized['run_path'])
            report = json.loads((root / 'demo' / 'summary.json').read_text())
            self.assertEqual([r['outcome'] for r in report['cases']], ['supported', 'unsupported', 'unresolved'])


if __name__ == '__main__':
    unittest.main()
