import json
from pathlib import Path
import unittest

from evaluation.privacy import create_review_template, prepare_privacy_workspace, privacy_review_path
from pilot import queue as q
from helpers import PilotFixture


class PrivacyQueueTests(PilotFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.discovery(self.run)
        q.select(self.run, ['111'])
        self.capture = self.root / 'private-source'
        (self.capture / 'photos').mkdir(parents=True)
        (self.capture / 'photos/01.jpg').write_bytes(b'synthetic original')
        q.write(self.capture / 'record.json', {
            'listing_id': '111', 'capture_id': 'fixture', 'collection_status': 'complete',
            'page_state': 'available', 'issues': [],
            'fields': {name: {'value': 'Synthetic 50 EUR'} for name in ('title', 'description', 'attributes')},
            'photos': [{'index': 1, 'status': 'downloaded', 'local_path': 'photos/01.jpg',
                        'sha256': q.sha(self.capture / 'photos/01.jpg')}]})
        self.workspace = self.root / 'editable'
        prepare_privacy_workspace(self.capture, self.workspace)
        (self.workspace / 'photos/01.jpg').write_bytes(b'synthetic manual redaction')
        review = create_review_template(self.capture, self.workspace)
        review.update(status='reviewed', actor='synthetic-operator', checked_at=q.now(),
                      notes='Synthetic review.', text_reviewed=True, text_changes='Price masked.',
                      evidence_preservation_assessed=True, evaluation_limitations=['One label is hidden.'])
        review['photos'][0].update(inspected=True, metadata_reviewed=True, transformation='redacted',
                                   notes='Synthetic image.', material_redaction_limitations=['Hidden label unknown.'])
        self.review = self.root / 'privacy.json'
        q.write(self.review, review)

    def bind_and_attest(self):
        bound = q.bind(self.run, '111', self.capture, self.workspace, self.review)
        checks = {key: True for key in ('source_verified', 'posting_time_verified', 'radius_verified',
                                        'price_mask_checked', 'privacy_checked')}
        checks.update(actor='synthetic-operator', checked_at=q.now(), evidence_notes='Synthetic only.',
                      source_capture_sha256=bound['source_capture_sha256'],
                      target_input_sha256=bound['target_input_sha256'], opened_photo_indices=[1])
        q.write(self.root / 'checks.json', checks)
        q.attest(self.run, '111', self.root / 'checks.json')

    def test_reviewed_derivative_can_complete_handoff_without_original_bytes_in_packet(self):
        before = q.tree(self.capture)
        self.bind_and_attest()
        self.reviewer()
        target = self.run / 'cases/111/reviewer/target'
        self.assertNotEqual(q.sha(target / 'photos/01.jpg'), q.sha(self.capture / 'photos/01.jpg'))
        self.assertFalse(privacy_review_path(target).exists())
        self.assertEqual(q.tree(self.capture), before)
        self.assertTrue(q.check_case(self.run, q.spec('111'))['ready'])

    def test_changed_private_review_blocks_later_dispatch(self):
        self.bind_and_attest()
        sibling = privacy_review_path(self.run / 'cases/111/target')
        sibling.write_text(sibling.read_text() + ' ', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'manifest hash mismatch'):
            q.job(self.run, '111', 'reviewer')


if __name__ == '__main__':
    unittest.main()
