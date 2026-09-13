import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from evaluation.manual import seal_review
from resale_tool.cli import evaluate_capture
from resale_tool.demo import create_demo


class ManualEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        create_demo(self.root / 'demo')
        self.capture = self.root / 'demo/supported-example'
        self.review = self.capture / 'review.json'

    def test_missing_declared_demo_evidence_is_detected(self):
        (self.capture / 'synthetic-evidence.txt').unlink()
        with self.assertRaisesRegex(ValueError, 'missing'):
            evaluate_capture(self.capture, self.review)

    def real_fixture(self):
        record = json.loads((self.capture / 'record.json').read_text(encoding='utf-8'))
        record.pop('synthetic')
        (self.capture / 'photos').mkdir()
        photo = self.capture / 'photos/01.png'
        photo.write_bytes(b'synthetic bytes for hash verification only')
        record['photos'] = [{'index': 1, 'status': 'downloaded', 'local_path': 'photos/01.png',
                             'sha256': hashlib.sha256(photo.read_bytes()).hexdigest()}]
        (self.capture / 'record.json').write_text(json.dumps(record), encoding='utf-8')
        review = json.loads(self.review.read_text(encoding='utf-8'))
        review.pop('synthetic')
        review['source_record_sha256'] = hashlib.sha256((self.capture / 'record.json').read_bytes()).hexdigest()
        self.review.write_text(json.dumps(review), encoding='utf-8')

    def test_unbound_manual_support_is_blocked_and_changed_evidence_is_rejected(self):
        self.real_fixture()
        with self.assertRaisesRegex(ValueError, 'seal-review'):
            evaluate_capture(self.capture, self.review)
        sealed = self.capture / 'sealed.json'
        sealed.write_text(json.dumps(seal_review(self.capture, self.review)), encoding='utf-8')
        self.assertEqual(evaluate_capture(self.capture, sealed)['outcome'], 'supported')
        (self.capture / 'synthetic-evidence.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'hash changed'):
            evaluate_capture(self.capture, sealed)

    def test_changed_gallery_blocks_manual_support(self):
        self.real_fixture()
        sealed = self.capture / 'sealed.json'
        sealed.write_text(json.dumps(seal_review(self.capture, self.review)), encoding='utf-8')
        (self.capture / 'photos/01.png').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'gallery path/hash mismatch'):
            evaluate_capture(self.capture, sealed)

    def test_source_reference_cannot_escape_evidence_root(self):
        self.real_fixture()
        review = json.loads(self.review.read_text(encoding='utf-8'))
        review['comps'][0]['source_paths'] = ['../outside.txt']
        self.review.write_text(json.dumps(review), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'confined'):
            seal_review(self.capture, self.review)


if __name__ == '__main__':
    unittest.main()
