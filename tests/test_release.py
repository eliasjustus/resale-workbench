import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools.stage_release import stage, is_link


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'source'
        self.root.mkdir()
        self.dest = Path(self.temp.name) / 'candidate'
        (self.root / 'module.py').write_text('VALUE = 1\n', encoding='utf-8')
        self.allow(['module.py'])

    def allow(self, names):
        (self.root / 'release-files.json').write_text(json.dumps({'schema_version': 1, 'files': names}), encoding='utf-8')

    def test_private_data_is_excluded_and_copied_bytes_match_manifest(self):
        (self.root / 'runs').mkdir()
        (self.root / 'runs' / 'private.txt').write_text('PRIVATE', encoding='utf-8')
        report = stage(self.root, self.dest)
        self.assertFalse((self.dest / 'runs').exists())
        self.assertEqual(hashlib.sha256((self.dest / 'module.py').read_bytes()).hexdigest(), report['files'][0]['sha256'])
        self.assertFalse(report['published'])
        with self.assertRaises(FileExistsError):
            stage(self.root, self.dest)

    def test_sensitive_text_rejects_before_copy_and_does_not_echo_secret(self):
        secret = 'gh' + 'p_' + 'A' * 32
        (self.root / 'module.py').write_text('value = "' + secret + '"\n', encoding='utf-8')
        with self.assertRaises(ValueError) as caught:
            stage(self.root, self.dest)
        self.assertNotIn(secret, str(caught.exception))
        self.assertIn('credential_format', str(caught.exception))
        self.assertFalse(self.dest.exists())

    def test_owner_markers_are_optional_private_input(self):
        with self.assertRaisesRegex(ValueError, 'local_private_marker'):
            stage(self.root, self.dest, private_patterns=['VALUE'])

    def test_forbidden_paths_cannot_be_added_to_allowlist(self):
        for name in ['../module.py', '/module.py', 'runs/case.json', 'private/key.py',
                     'auth.local.json', 'image.jpg', 'C:/module.py', '.env', 'a/../module.py']:
            with self.subTest(name=name):
                self.allow([name])
                with self.assertRaises(ValueError):
                    stage(self.root, self.dest)
                self.assertFalse(self.dest.exists())

    def test_case_colliding_paths_are_refused(self):
        self.allow(['module.py', 'MODULE.py'])
        with self.assertRaises(ValueError):
            stage(self.root, self.dest)

    def test_reparse_point_is_rejected_even_without_pathlib_junction_api(self):
        with mock.patch.object(Path, 'lstat', return_value=type('Attrs', (), {'st_file_attributes': 0x400, 'st_mode': 0o040000})()):
            self.assertTrue(is_link(self.root))

    def test_parent_junction_cannot_alias_a_private_file(self):
        docs = self.root / 'docs'
        docs.mkdir()
        (docs / 'example.md').write_text('Synthetic private fixture', encoding='utf-8')
        self.allow(['docs/example.md'])
        from tools import stage_release
        original = stage_release.is_link
        with mock.patch.object(stage_release, 'is_link', side_effect=lambda p: p == docs or original(p)):
            with self.assertRaisesRegex(ValueError, 'Symlinks'):
                stage(self.root, self.dest)
        self.assertFalse(self.dest.exists())


if __name__ == '__main__':
    unittest.main()
