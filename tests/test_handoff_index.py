"""Generated indices preserve declarations and retained human-authored prose."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from evaluation.handoff import finalize, render_index


class HandoffIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.handoff = {
            'schema_version': 2, 'case_id': 'synthetic-case', 'outcome': 'unresolved',
            'target': {}, 'research': {'stop_reason': 'Synthetic source gap'},
            'evidence': [
                {'evidence_id': 'observation-1', 'kind': 'modelled_estimate',
                 'source_paths': ['source.txt'], 'image_paths': [],
                 'limitations': ['Model output; no verified transaction.'],
                 'observation': 'This substantive narrative must stay in the JSON.'},
                {'evidence_id': 'observation-2', 'kind': 'asking_context',
                 'source_paths': ['source.txt'], 'image_paths': [],
                 'limitations': ['An asking price is not an actual sale.']},
            ],
        }
        (self.folder / 'source.txt').write_text('Synthetic source placeholder only.', encoding='utf-8')
        (self.folder / 'lessons.md').write_text('Synthetic lesson.', encoding='utf-8')
        self.write_handoff()

    def write_handoff(self):
        (self.folder / 'handoff.json').write_text(json.dumps(self.handoff), encoding='utf-8')

    def test_missing_index_is_deterministic_and_ready_binds_it(self):
        ready = finalize(self.folder)
        index_path = self.folder / 'HANDOFF.md'
        index = index_path.read_text(encoding='utf-8')
        self.assertEqual(index, render_index(self.handoff))
        self.assertEqual(index_path.read_bytes(), render_index(self.handoff).encode('utf-8'))
        self.assertIn('modelled\\_estimate', index)
        self.assertIn('asking\\_context', index)
        self.assertIn('Model output; no verified transaction.', index)
        self.assertNotIn('This substantive narrative', index)
        self.assertIn('does not independently verify facts', index)
        manifest = {entry['path']: entry['sha256'] for entry in ready['files']}
        self.assertEqual(manifest['HANDOFF.md'], hashlib.sha256(index_path.read_bytes()).hexdigest())
        self.assertNotIn('lessons.md', manifest)  # Existing lessons/READY contract stays unchanged.

    def test_target_binding_is_reflected_before_index_generation(self):
        target = self.folder / 'target'
        (target / 'photos').mkdir(parents=True)
        photo = target / 'photos/01.jpg'
        photo.write_bytes(b'synthetic image placeholder')
        (target / 'input.json').write_text(json.dumps({'photos': [
            {'index': 1, 'status': 'downloaded', 'path': 'photos/01.jpg',
             'sha256': hashlib.sha256(photo.read_bytes()).hexdigest()}]}), encoding='utf-8')
        self.handoff['target'] = {'input_path': 'target/input.json', 'photo_paths': []}
        self.write_handoff()
        finalize(self.folder)
        self.assertIn('Listed target photos: 1', (self.folder / 'HANDOFF.md').read_text(encoding='utf-8'))
        bound = json.loads((self.folder / 'handoff.json').read_text(encoding='utf-8'))
        self.assertEqual(bound['target']['photo_paths'], ['target/photos/01.jpg'])

    def test_existing_authored_index_is_never_replaced(self):
        original = b'# Original authored index\r\n\r\nKeep these exact bytes.\r\n'
        index_path = self.folder / 'HANDOFF.md'
        index_path.write_bytes(original)
        first = finalize(self.folder)
        self.assertEqual(index_path.read_bytes(), original)
        self.handoff['research']['stop_reason'] = 'Corrected factual declaration'
        self.write_handoff()
        second = finalize(self.folder)
        self.assertEqual(index_path.read_bytes(), original)
        for ready in (first, second):
            index = next(item for item in ready['files'] if item['path'] == 'HANDOFF.md')
            self.assertEqual(index['sha256'], hashlib.sha256(original).hexdigest())

    def test_generated_index_is_not_silently_regenerated_on_refinalize(self):
        finalize(self.folder)
        original = (self.folder / 'HANDOFF.md').read_bytes()
        self.handoff['research']['stop_reason'] = 'Later edited JSON'
        self.write_handoff()
        finalize(self.folder)
        self.assertEqual((self.folder / 'HANDOFF.md').read_bytes(), original)

    def test_finalized_missing_index_fails_before_any_packet_write(self):
        finalize(self.folder)
        (self.folder / 'HANDOFF.md').unlink()
        before = {path.name: path.read_bytes() for path in self.folder.iterdir() if path.is_file()}
        with self.assertRaisesRegex(ValueError, 'Finalized packet is missing HANDOFF.md'):
            finalize(self.folder)
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.folder.iterdir() if path.is_file()})

    def test_legacy_finalized_packet_without_an_index_keeps_old_behavior(self):
        ready = {'schema_version': 1, 'case_id': self.handoff['case_id'],
                 'files': [{'path': 'handoff.json', 'sha256': hashlib.sha256(
                     (self.folder / 'handoff.json').read_bytes()).hexdigest()}]}
        (self.folder / 'READY.json').write_text(json.dumps(ready), encoding='utf-8')
        result = finalize(self.folder)
        self.assertFalse((self.folder / 'HANDOFF.md').exists())
        self.assertNotIn('HANDOFF.md', {item['path'] for item in result['files']})
        self.assertIn('source.txt', {item['path'] for item in result['files']})

    def test_untrusted_values_cannot_add_headings_html_or_markdown_links(self):
        self.handoff['case_id'] = 'fixture\n# injected\n<script>no</script>'
        self.handoff['evidence'][0]['limitations'] = ['[Open](javascript:example)\n# more']
        index = render_index(self.handoff)
        self.assertNotIn('<script>', index)
        self.assertNotIn('\n# injected', index)
        self.assertNotIn('[Open](', index)
        self.assertIn('\\<script\\>', index)
        self.assertIn('\\[Open\\]\\(', index)

    def test_legacy_candidates_are_not_relabelled_transactions(self):
        self.handoff = {'schema_version': 1, 'case_id': 'legacy-synthetic',
                        'target': {}, 'outcome': 'unresolved',
                        'candidates': [{'sale_id': 'candidate-1', 'price_record': 'source.txt'}]}
        self.write_handoff()
        finalize(self.folder)
        index = (self.folder / 'HANDOFF.md').read_text(encoding='utf-8')
        self.assertIn('Legacy candidate references', index)
        self.assertIn('not a generated finding of comparable transactions', index)
        self.assertIn('No evidence entries are indexed', index)


if __name__ == '__main__':
    unittest.main()
