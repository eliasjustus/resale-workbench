"""Synthetic provenance/security fixtures, never real marketplace content."""

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from evaluation.packet import export_packet
from evaluation.privacy import (create_review_template, export_reviewed_packet,
                                prepare_privacy_workspace, privacy_review_path,
                                text_findings, validate_privacy_packet)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.capture = self.root / 'capture'
        (self.capture / 'photos').mkdir(parents=True)
        self.photo = self.capture / 'photos/01.jpg'
        self.photo.write_bytes(b'synthetic original evidence bytes')
        self.record = {
            'listing_id': 'synthetic', 'capture_id': 'fixture', 'collection_status': 'complete',
            'fields': {name: {'value': 'Synthetic device 50 EUR'}
                       for name in ('title', 'description', 'attributes')},
            'photos': [{'index': 1, 'status': 'downloaded', 'local_path': 'photos/01.jpg',
                        'sha256': sha(self.photo)}],
        }
        write(self.capture / 'record.json', self.record)
        self.workspace = self.root / 'editable'
        prepare_privacy_workspace(self.capture, self.workspace)
        self.review_path = self.root / 'review.json'
        self.destination = self.root / 'target'

    def review(self, redacted=True):
        if redacted:
            (self.workspace / 'photos/01.jpg').write_bytes(b'synthetic manually redacted evidence bytes')
        review = create_review_template(self.capture, self.workspace)
        review.update(status='reviewed', actor='local-reviewer', checked_at='2026-01-01T12:00:00Z',
                      notes='Synthetic test review.', text_reviewed=True,
                      text_changes='Synthetic fixture; removed private label.',
                      evidence_preservation_assessed=True,
                      evaluation_limitations=['One label is hidden; identity there is unknown.'])
        for photo in review['photos']:
            photo.update(inspected=True, metadata_reviewed=True,
                         transformation='redacted' if redacted else 'unchanged',
                         notes='Synthetic visual and metadata inspection.',
                         material_redaction_limitations=['Hidden label cannot support identity.'])
        write(self.review_path, review)
        return review

    def export(self):
        return export_reviewed_packet(self.capture, self.destination, self.workspace, self.review_path)

    def test_reviewed_derivative_preserves_source_and_separates_private_review(self):
        before = {str(path.relative_to(self.capture)): sha(path)
                  for path in self.capture.rglob('*') if path.is_file()}
        self.review()
        packet = self.export()
        self.assertEqual(before, {str(path.relative_to(self.capture)): sha(path)
                                 for path in self.capture.rglob('*') if path.is_file()})
        self.assertTrue(packet['photos'][0]['is_derivative'])
        self.assertNotEqual(packet['photos'][0]['sha256'], packet['photos'][0]['source_sha256'])
        self.assertFalse(packet['blind_review_ready'])
        self.assertNotIn('actor', packet)
        self.assertNotIn(str(self.capture), json.dumps(packet))
        self.assertNotIn('local-reviewer', json.dumps(packet))
        self.assertEqual(validate_privacy_packet(self.capture, self.destination)['status'], 'reviewed')
        self.assertTrue(privacy_review_path(self.destination).is_file())
        self.assertEqual({p.relative_to(self.destination).as_posix()
                          for p in self.destination.rglob('*') if p.is_file()},
                         {'input.json', 'photos/01.jpg'})

    def test_review_without_redaction_remains_explicit(self):
        self.review(redacted=False)
        self.assertFalse(self.export()['photos'][0]['is_derivative'])
        validate_privacy_packet(self.capture, self.destination)

    def test_pending_template_never_exports_and_extra_files_rejected(self):
        write(self.review_path, create_review_template(self.capture, self.workspace))
        with self.assertRaisesRegex(ValueError, 'explicitly be reviewed'):
            self.export()
        self.assertFalse(self.destination.exists())
        (self.workspace / 'private-notes.txt').write_text('synthetic unreviewed note')
        with self.assertRaisesRegex(ValueError, 'Unexpected unreviewed'):
            create_review_template(self.capture, self.workspace)

    def test_reviewed_workspace_cannot_change_before_export(self):
        self.review()
        write(self.workspace / 'source-text.json', {'title': 'changed', 'description': None, 'attributes': None})
        with self.assertRaisesRegex(ValueError, 'text hash mismatch'):
            self.export()

    def test_all_photo_and_metadata_reviews_are_required(self):
        for key in ('inspected', 'metadata_reviewed'):
            with self.subTest(key=key):
                review = self.review()
                review['photos'][0][key] = False
                write(self.review_path, review)
                with self.assertRaisesRegex(ValueError, 'visual and metadata'):
                    self.export()
        review = self.review()
        review['photos'] = []
        write(self.review_path, review)
        with self.assertRaisesRegex(ValueError, 'every photo'):
            self.export()

    def test_mislabelled_redaction_rejected(self):
        review = self.review()
        review['photos'][0]['transformation'] = 'unchanged'
        write(self.review_path, review)
        with self.assertRaisesRegex(ValueError, 'contradicts image hashes'):
            self.export()

    def test_image_and_source_tampering_after_export_rejected(self):
        self.review()
        self.export()
        derived = self.destination / 'photos/01.jpg'
        saved = derived.read_bytes()
        derived.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'file/hash mismatch'):
            validate_privacy_packet(self.capture, self.destination)
        derived.write_bytes(saved)
        self.photo.write_bytes(b'changed original')
        with self.assertRaisesRegex(ValueError, 'Original photo path/hash mismatch'):
            validate_privacy_packet(self.capture, self.destination)

    def test_manifest_and_text_tampering_rejected(self):
        self.review()
        self.export()
        review_path = privacy_review_path(self.destination)
        saved = review_path.read_bytes()
        review_path.write_bytes(saved + b' ')
        with self.assertRaisesRegex(ValueError, 'manifest hash mismatch'):
            validate_privacy_packet(self.capture, self.destination)
        review_path.write_bytes(saved)
        packet = json.loads((self.destination / 'input.json').read_text(encoding='utf-8'))
        packet['source_text']['title'] = 'Modified after review'
        write(self.destination / 'input.json', packet)
        with self.assertRaisesRegex(ValueError, 'text hash mismatch'):
            validate_privacy_packet(self.capture, self.destination)

    def test_unreviewed_packet_files_and_fields_rejected(self):
        self.review()
        self.export()
        extra = self.destination / 'extra.json'
        extra.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'Unexpected unreviewed'):
            validate_privacy_packet(self.capture, self.destination)
        extra.unlink()
        packet = json.loads((self.destination / 'input.json').read_text(encoding='utf-8'))
        packet['private_original'] = 'unreviewed'
        write(self.destination / 'input.json', packet)
        with self.assertRaisesRegex(ValueError, 'unreviewed fields'):
            validate_privacy_packet(self.capture, self.destination)

    def test_relative_escape_absolute_path_and_backslash_rejected(self):
        self.review()
        original = self.export()
        for path in ('../capture/photos/01.jpg', str(self.photo.resolve()), 'photos\\01.jpg'):
            with self.subTest(path=path):
                packet = copy.deepcopy(original)
                packet['photos'][0]['path'] = path
                write(self.destination / 'input.json', packet)
                with self.assertRaises(ValueError):
                    validate_privacy_packet(self.capture, self.destination)

    def test_source_symlink_rejected_even_inside_capture(self):
        linked = self.capture / 'photos/02.jpg'
        try:
            os.symlink(self.photo, linked)
        except OSError as exc:
            self.skipTest(f'Host cannot create symlinks: {exc}')
        self.record['photos'][0]['local_path'] = 'photos/02.jpg'
        write(self.capture / 'record.json', self.record)
        with self.assertRaisesRegex(ValueError, 'symlinks or junctions'):
            create_review_template(self.capture, self.workspace)

    def test_duplicate_or_missing_gallery_positions_rejected(self):
        self.record['photos'].append(dict(self.record['photos'][0]))
        write(self.capture / 'record.json', self.record)
        with self.assertRaisesRegex(ValueError, 'indices'):
            create_review_template(self.capture, self.workspace)

    def test_flags_never_repeat_matches_or_claim_privacy(self):
        findings = text_findings({'title': 'Contact synthetic@example.invalid',
                                  'description': 'Password: SYNTHETIC_ONLY', 'attributes': None})
        self.assertEqual({f['category'] for f in findings}, {'email_like', 'credential_label'})
        self.assertNotIn('SYNTHETIC_ONLY', json.dumps(findings))
        self.assertNotIn('synthetic@example.invalid', json.dumps(findings))
        packet = export_packet(self.capture, self.root / 'legacy-draft')
        self.assertEqual(packet['privacy_status'], 'unreviewed')
        self.assertEqual((self.root / 'legacy-draft/photos/01.jpg').read_bytes(), self.photo.read_bytes())

    def test_missing_limitations_future_time_and_added_review_fields_rejected(self):
        for update in ({'evaluation_limitations': []}, {'checked_at': '2999-01-01T00:00:00Z'},
                       {'checked_at': '2026-01-01T00:00:00'}, {'unreviewed_field': 'no'}):
            with self.subTest(update=update):
                review = self.review()
                review.update(update)
                write(self.review_path, review)
                with self.assertRaises(ValueError):
                    self.export()


if __name__ == '__main__':
    unittest.main()
