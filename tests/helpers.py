"""Explicit synthetic fixtures shared by tests; no discoverable test classes.

Mixins rely on the owning unittest.TestCase only for addCleanup. Fixture builders
never import another test module and never contribute test_* methods.
"""
from pathlib import Path
import shutil
import tempfile
import tomllib
from unittest.mock import patch

from evaluation.handoff import finalize
from pilot import queue as q
from pilot.config import example_config


def repair():
    return {'schema_version': 1, 'status': 'unresolved', 'symptoms': [], 'hypotheses': [],
            'diagnostics': [], 'diagnosis_evidence': [], 'verification_evidence': [],
            'downside': {'status': 'unresolved', 'evidence_refs': []}, 'unknowns': []}



class PilotFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.playbooks = self.root / 'source-playbooks'
        self.playbooks.mkdir()
        for name in ('REVIEWER.md', 'VALUATOR.md'):
            (self.playbooks / name).write_text(name, encoding='utf-8')
        self.db = self.root / 'state.sqlite3'
        self.run = self.new_run('one')

    def new_run(self, name, require_direct_research_capture=False):
        run = self.root / name
        q.init(run, self.db, '2026-09-12T12:00:30Z', self.playbooks,
               require_direct_research_capture=require_direct_research_capture, workflow_version=1)
        return run

    def discovery(self, run, identities=('111',), extra=None):
        rows = [{'listing_id': i, 'text': 'Heute, 13:50', 'observed_at': '2026-09-12T12:10:00Z',
                 'url': 'https://www.kleinanzeigen.de/s-anzeige/item/' + i + '-1-1'} for i in identities]
        rows.extend(extra or [])
        q.write(run / 'rows.json', rows)
        q.write(run / 'summary.json', {'row_occurrences': len(rows), 'coverage_complete': False,
                                      'stop_reason': 'bounded_test'})
        return q.import_discovery(run, run / 'rows.json', run / 'summary.json')

    def bind(self, identity='111'):
        capture = self.root / ('capture-' + identity)
        (capture / 'photos').mkdir(parents=True)
        (capture / 'photos' / '01.jpg').write_bytes(b'fake photo: only hash validation in this unit test')
        record = {'listing_id': identity, 'capture_id': 'c1', 'collection_status': 'complete',
                  'page_state': 'available', 'issues': [],
                  'fields': {key: {'value': val} for key, val in
                             [('title', 'Example tech'), ('description', 'Costs 50 EUR'), ('attributes', 'Used')]},
                  'photos': [{'index': 1, 'status': 'downloaded', 'local_path': 'photos/01.jpg',
                              'sha256': q.sha(capture / 'photos' / '01.jpg')}]}
        q.write(capture / 'record.json', record)
        result = q.bind(self.run, identity, capture)
        checks = {key: True for key in ('source_verified', 'posting_time_verified', 'radius_verified',
                                        'price_mask_checked', 'privacy_checked')}
        checks.update(actor='unit-test', checked_at=q.now(), evidence_notes='synthetic only',
                      source_capture_sha256=result['source_capture_sha256'],
                      target_input_sha256=result['target_input_sha256'], opened_photo_indices=[1])
        q.write(self.run / f'{identity}-checks.json', checks)
        return capture

    def prepared(self, identities=('111',)):
        self.discovery(self.run, identities)
        q.select(self.run, list(identities))
        for identity in identities:
            self.bind(identity)
            q.attest(self.run, identity, self.run / f'{identity}-checks.json')

    def reviewer(self, identity='111', candidates=True, complete=True):
        q.job(self.run, identity, 'reviewer')
        q.dispatched(self.run, identity, 'reviewer', f'agent-{identity}')
        folder = self.run / 'cases' / identity / 'reviewer'
        shutil.copytree(self.run / 'cases' / identity / 'target', folder / 'target')
        q.write(folder / 'source.json', {'example': 'synthetic sold evidence'})
        handoff = {'schema_version': 1, 'case_id': identity, 'role': 'evidence_preparation',
                   'playbook_version': 'test', 'valuation_performed': False,
                   'purchase_authorized': False, 'target_price_seen': False,
                   'target': {'input_path': 'target/input.json', 'opened_indices': [1]},
                   'research': {}, 'grouped_uninspected_rows': [], 'outcome': 'unresolved',
                   'repair': repair(), 'candidates': []}
        if candidates:
            handoff['candidates'] = [{'sale_id': 'sold-1', 'source_path': 'source.json',
                'price_record': {'item_price': 100, 'currency': 'EUR'},
                'gallery_paths': [], 'gallery_opened_indices': [],
                'visual_audit_scope': {'status': 'not_required', 'reason': 'wrong_model',
                                       'evidence': 'synthetic wrong-model record retained'}}]
        q.write(folder / 'handoff.json', handoff)
        finalize(folder)
        if not complete:
            return folder
        return q.complete_review(self.run, identity)

    def direct_research(self, folder, identity='111'):
        """Synthetic direct-capture fixture; no browser or genuine market evidence."""
        capture = folder / 'research/search-01'
        capture.mkdir(parents=True)
        stamp = q.now()
        files = {}
        for kind, name in [('dom_snapshot', 'dom.txt'), ('rendered_text', 'rendered.txt')]:
            raw = capture / name
            raw.write_text('Synthetic empty sold search', encoding='utf-8')
            files[kind] = {'path': name, 'bytes': raw.stat().st_size, 'sha256': q.sha(raw), 'captured_at': stamp}
        q.write(capture / 'capture.json', {'schema_version': 1,
            'capture_method': q.research_sources.CAPTURE_METHOD, 'started_at': stamp, 'completed_at': stamp,
            'source_url': 'https://example.test/sold', 'source_url_after': 'https://example.test/sold',
            'source_title': 'Synthetic search', 'context': {}, 'files': files})
        q.write(folder / 'research/packet.json', {'schema_version': 1, 'role': 'sold_search', 'case_id': identity,
            'attempts': [{'capture_path': 'search-01/capture.json', 'query': 'synthetic test',
                          'displayed_period': None, 'displayed_filters': None, 'result_kind': 'empty',
                          'rows': [], 'limits': ['Synthetic fixture only']}]})
        handoff = q.read(folder / 'handoff.json')
        handoff['research'] = {'attempted': True, 'capture_packets': ['research/packet.json']}
        q.write(folder / 'handoff.json', handoff)
        finalize(folder)
        return capture

    def direct_prepared(self, identities=('111',)):
        self.run = self.new_run('direct', require_direct_research_capture=True)
        self.prepared(identities)

    def valuation(self, identity='111'):
        q.job(self.run, identity, 'valuator')
        q.dispatched(self.run, identity, 'valuator', 'sol-' + identity)
        packet = self.run / 'cases' / identity / 'reviewer'
        value = {'schema_version': 1, 'case_id': identity, 'role': 'valuation',
                 'model_requested': 'gpt-5.6-sol', 'effort_requested': 'high',
                 'packet_sha256': q.sha(packet / 'READY.json'), 'input_integrity_passed': True,
                 'target_price_seen': False, 'target_assessment': {},
                 'inspected_images': [{'path': 'target/photos/01.jpg', 'sha256': q.sha(packet / 'target/photos/01.jpg')}],
                 'comparisons': [{'sale_id': 'sold-1', 'disposition': 'rejected', 'reason': 'wrong model',
                                  'condition_basis': 'current_condition', 'evidence_refs': ['source.json']}],
                 'estimate': None, 'observed_reference_prices': [], 'missing_evidence': ['No matching sales'],
                 'economic_outcome': 'unresolved', 'gate_eligible': False, 'purchase_authorized': False,
                 'measured_started_at': q.now(), 'measured_completed_at': q.now(), 'repair': repair()}
        folder = self.run / 'cases' / identity / 'valuator'
        q.write(folder / 'valuation.json', value)
        for name in ('valuation.md', 'lessons.md'):
            (folder / name).write_text('Synthetic test output', encoding='utf-8')
        return folder, value


class ConfiguredPilotFixture(PilotFixture):
    def setUp(self):
        clock = patch.object(q, 'now', return_value='2026-09-12T12:01:00+00:00')
        clock.start()
        self.addCleanup(clock.stop)
        super().setUp()
        self.config = tomllib.loads(example_config())
        self.config['budget'].update(max_cases=3, reviewer_job_limit=3)
        self.config['data_dir'] = str(self.root / 'user-data')
        self.config['roles']['valuator'] = {'model': 'test-local-model', 'reasoning_effort': 'medium'}
        self.run = self.root / 'configured'
        q.init(self.run, start='2026-09-12T12:00:30Z', playbook_root=self.playbooks,
               config=self.config, workflow_version=1, category='computers')


def build_v2_review(self, evidence=True, complete=True):
    q.job(self.run, '111', 'reviewer')
    q.dispatched(self.run, '111', 'reviewer', 'synthetic-luna')
    folder = self.run / 'cases/111/reviewer'
    shutil.copytree(self.run / 'cases/111/target', folder / 'target')
    (folder / 'raw.txt').write_text('Synthetic model output 100-120 EUR; not transaction evidence.', encoding='utf-8')
    handoff = {'schema_version': 2, 'case_id': '111', 'role': 'evidence_preparation',
               'playbook_version': '3.0', 'valuation_performed': False, 'purchase_authorized': False,
               'target_price_seen': False, 'target': {'input_path': 'target/input.json', 'opened_indices': [1]},
               'research': {'attempts': [{'approach': 'Synthetic appropriate model source',
                   'result': 'Model only', 'next_decision': 'Ask valuator to assess limits',
                   'source_paths': ['raw.txt']}], 'stop_reason': 'Useful source retained; fixture budget reached'},
               'evidence': [], 'outcome': 'unresolved', 'repair': repair()}
    if evidence:
        handoff['evidence'] = [{'evidence_id': 'local-model', 'kind': 'modelled_estimate',
            'locator': 'synthetic:fixture/model', 'captured_at': q.now(), 'source_paths': ['raw.txt'],
            'image_paths': [], 'image_inspection': 'No source imagery available',
            'observation': 'Synthetic model suggests 100-120 EUR', 'supports': 'Conditional working value',
            'limitations': ['Synthetic, not a sale or validated market value']}]
    q.write(folder / 'handoff.json', handoff)
    finalize(folder)
    if complete:
        return q.complete_review(self.run, '111')
    return folder


class WorkflowTwoFixture(PilotFixture):
    def setUp(self):
        super().setUp()
        self.run = self.root / 'v2'
        q.init(self.run, self.db, '2026-09-12T12:00:30Z', self.playbooks, category='future_category')

    reviewer = build_v2_review

    def valuation(self, evidence=True, estimate=False):
        folder, value = super().valuation()
        value['schema_version'] = 2
        value['evidence_assessment'] = {'status': 'sufficient_for_conditional_estimate' if estimate else 'insufficient',
                                       'reason': 'Synthetic adequacy judgment', 'limitations': ['Not market validation']}
        value['comparisons'] = ([{'evidence_id': 'local-model', 'kind': 'modelled_estimate',
            'disposition': 'conditional' if estimate else 'reference_only', 'condition_basis': 'current_condition',
            'reason': 'Conditional synthetic transfer only', 'evidence_refs': ['raw.txt']}] if evidence else [])
        if estimate:
            value['estimate'] = {'kind': 'conditional_target_estimate', 'currency': 'EUR',
                'condition_basis': 'current_condition', 'item_price_low': 100, 'item_price_high': 120,
                'central_estimate': None, 'method': 'Synthetic transfer with explicit limitations',
                'source_evidence_ids': ['local-model'], 'assumptions': ['Same unknown condition'],
                'limitations': ['Modelled reference, not transaction evidence']}
        q.write(folder / 'valuation.json', value)
        return folder, value


class EconomicFixture:
    def setUp(self):
        self.record = {'listing_id': '1', 'capture_id': 'a', 'collection_status': 'complete',
                       'fields': {'asking_price': {'value': '50 € VB'}}}
        self.review = {'listing_id': '1', 'capture_id': 'a', 'as_of': '2026-09-12',
                       'work': {'required': False, 'evidence': ['synthetic working item']},
                       'scope': {'status': 'included', 'evidence': ['title']},
                       'contradictions': [], 'acquisition_price': {'eur': '50', 'source_text': '50 € VB'},
                       'costs': {name: {'eur': '2', 'basis': 'test scenario'} for name in (
                           'acquisition_transport', 'resale_shipping', 'fees', 'packaging', 'defect_allowance')},
                       'comps': [{'sale_id': str(i), 'units_sold': 1, 'review_status': 'accepted',
                                  'review_evidence': ['synthetic audited exact match'],
                                  'price_basis': 'actual_sold_item_price', 'currency': 'EUR',
                                  'seller_country': 'DE', 'sold_at': '2026-09-01', 'item_price_eur': '80'}
                                 for i in range(5)]}
        for name in ('identity', 'condition', 'gallery'):
            self.review[name] = {'status': 'resolved', 'evidence': ['synthetic fixture']}

