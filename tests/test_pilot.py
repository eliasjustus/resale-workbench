"""Queue safety tests: external dispatch is simulated, never calls a model."""
import json
from contextlib import closing, redirect_stdout
import io
from pathlib import Path
import unittest

from evaluation.handoff import finalize
from pilot import queue as q
from helpers import PilotFixture


class PilotTests(PilotFixture, unittest.TestCase):


    def test_automotive_category_persists_in_report_and_frozen_state(self):
        run = self.root / 'automotive'
        q.init(run, self.db, '2026-09-12T12:00:30Z', self.playbooks, category='automotive')
        report = q.report(run)
        self.assertEqual(report['manifest']['focus'], 'tech_and_automotive')
        self.assertEqual(report['manifest']['sampled_category'], 'automotive')
        self.assertIn('sampled category: automotive', (run / 'pilot-report.md').read_text())
        manifest = q.read(run / 'manifest.json')
        manifest['sampled_category'] = 'gaming_pcs'
        q.write(run / 'manifest.json', manifest)
        with self.assertRaisesRegex(ValueError, 'Frozen manifest changed'):
            q.report(run)

    def test_reviewer_job_uses_current_bounded_research_sequence(self):
        self.prepared()
        prompt = q.job(self.run, '111', 'reviewer')['job']['prompt']
        self.assertIn('three captured result states', prompt)
        self.assertIn('two identity-appropriate queries', prompt)
        self.assertNotIn('two exact-model queries, two result pages', prompt)


    def test_frozen_window_and_playbooks(self):
        manifest = q.read(self.run / 'manifest.json')
        self.assertFalse(manifest['require_direct_research_capture'])
        self.assertEqual(manifest['window_start_inclusive'], '2026-09-11T12:00:30+00:00')
        self.assertEqual(manifest['window_end_exclusive'], '2026-09-12T12:00:30+00:00')
        self.assertEqual(manifest['radius_km'], 100)
        (self.run / 'playbooks/REVIEWER.md').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'playbook changed'):
            q.report(self.run)

    def test_reinitialization_rejected(self):
        with self.assertRaises(ValueError):
            q.init(self.run, self.db, playbook_root=self.playbooks)

    def test_import_idempotent_and_all_rows_accounted(self):
        self.discovery(self.run, ('111', '111'), [{'listing_id': 'bad', 'text': 'unknown'}])
        result = q.import_discovery(self.run, self.run / 'rows.json', self.run / 'summary.json')
        self.assertTrue(result['already_imported'])
        report = q.report(self.run)
        self.assertEqual(report['row_occurrences'], 3)
        self.assertEqual(report['sightings'][2]['status'], 'unresolved_invalid_listing_id')

    def test_claim_dedup_cross_run_and_seen_unclaimed_can_select(self):
        self.discovery(self.run, ('111', '222'))
        q.select(self.run, ['111'])
        other = self.new_run('two')
        self.discovery(other, ('111', '222'))
        with self.assertRaisesRegex(ValueError, 'already claimed'):
            q.select(other, ['111'])
        q.select(other, ['222'])
        self.assertEqual(q.report(other)['sightings'][0]['disposition'], 'duplicate_claimed_prior_run')

    def test_recomputes_eligibility_ignores_supplied_status(self):
        self.discovery(self.run, (), [{'listing_id': '111', 'text': 'Heute, 15:00',
            'observed_at': '2026-09-12T13:05:00Z', 'eligibility': {'status': 'eligible'}}])
        with self.assertRaisesRegex(ValueError, 'no eligible'):
            q.select(self.run, ['111'])

    def test_premature_job_and_selection_freeze(self):
        self.discovery(self.run, ('111', '222'))
        q.select(self.run, ['111'])
        with self.assertRaises(ValueError):
            q.job(self.run, '111', 'reviewer')
        self.bind()
        q.attest(self.run, '111', self.run / '111-checks.json')
        q.job(self.run, '111', 'reviewer')
        with self.assertRaisesRegex(ValueError, 'froze'):
            q.select(self.run, ['222'])

    def test_target_tamper_detected_before_dispatch(self):
        self.prepared()
        q.job(self.run, '111', 'reviewer')
        (self.run / 'cases/111/target/input.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'changed'):
            q.dispatched(self.run, '111', 'reviewer', 'agent')

    def test_dispatch_resume_does_not_create_new_job(self):
        self.prepared()
        first = q.job(self.run, '111', 'reviewer')
        second = q.job(self.run, '111', 'reviewer')
        self.assertTrue(second['resume_only'])
        self.assertEqual(first['job'], second['job'])
        q.dispatched(self.run, '111', 'reviewer', 'agent')
        with self.assertRaisesRegex(ValueError, 'Different agent'):
            q.dispatched(self.run, '111', 'reviewer', 'agent-2')

    def test_all_reviewers_must_finish_before_any_sol(self):
        self.prepared(('111', '222'))
        self.reviewer('111')
        with self.assertRaisesRegex(ValueError, 'All selected reviewers'):
            q.job(self.run, '111', 'valuator')
        q.stop(self.run, '222', 'Unavailable during preparation')
        self.assertFalse(q.job(self.run, '111', 'valuator')['resume_only'])

    def test_reviewer_packet_tamper_blocks_sol(self):
        self.prepared()
        self.reviewer()
        (self.run / 'cases/111/reviewer/source.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'Handoff check failed'):
            q.job(self.run, '111', 'valuator')

    def test_no_candidates_terminal_no_sol(self):
        self.prepared()
        self.assertEqual(self.reviewer(candidates=False)['stage'], 'unresolved')
        with self.assertRaises(ValueError):
            q.job(self.run, '111', 'valuator')

    def test_complete_lifecycle_and_never_claim_profit(self):
        self.prepared()
        self.reviewer()
        self.valuation()
        result = q.complete_valuation(self.run, '111')
        self.assertEqual(result['stage'], 'complete')
        self.assertEqual(result['outcome'], 'unresolved')
        self.assertFalse(q.report(self.run)['purchase_authorized'])

    def test_valuation_false_gate_or_bad_timing_rejected(self):
        self.prepared()
        self.reviewer()
        folder, value = self.valuation()
        value['economic_outcome'] = 'supported'
        q.write(folder / 'valuation.json', value)
        with self.assertRaisesRegex(ValueError, 'economic_outcome must be one of'):
            q.complete_valuation(self.run, '111')
        value['economic_outcome'] = 'unresolved'
        value['measured_started_at'] = '2026-09-11T00:00:00Z'
        q.write(folder / 'valuation.json', value)
        with self.assertRaisesRegex(ValueError, 'measured times'):
            q.complete_valuation(self.run, '111')

    def test_active_agent_cannot_be_silently_stopped(self):
        self.prepared()
        q.job(self.run, '111', 'reviewer')
        q.dispatched(self.run, '111', 'reviewer', 'agent')
        with self.assertRaises(ValueError):
            q.stop(self.run, '111', 'pretend terminated')
        with self.assertRaises(ValueError):
            q.stop_agent(self.run, '111', 'wrong', 'failed tool result')
        q.stop_agent(self.run, '111', 'agent', 'Coordinator observed terminal failure in tool result')
        self.assertEqual(q.report(self.run)['cases'][0]['stage'], 'failed')

    def test_estimate_cannot_use_rejected_candidate(self):
        self.prepared()
        self.reviewer()
        folder, value = self.valuation()
        value['estimate'] = {'kind': 'conditional_target_estimate', 'currency': 'EUR',
            'condition_basis': 'current_condition', 'item_price_low': 80, 'item_price_high': 100,
            'central_estimate': 90, 'method': 'Synthetic transfer', 'source_sale_ids': ['sold-1'],
            'assumptions': ['Synthetic assumption'], 'limitations': ['Synthetic test']}
        q.write(folder / 'valuation.json', value)
        with self.assertRaisesRegex(ValueError, 'Estimate source'):
            q.complete_valuation(self.run, '111')

    def test_all_candidates_get_exactly_one_disposition(self):
        self.prepared()
        self.reviewer()
        folder, value = self.valuation()
        original = value['comparisons'][0]
        for comparisons in ([], [original, original]):
            value['comparisons'] = comparisons
            q.write(folder / 'valuation.json', value)
            with self.assertRaisesRegex(ValueError, 'exactly one'):
                q.complete_valuation(self.run, '111')

    def test_conditional_repair_estimate_requires_full_source_contract(self):
        self.prepared()
        self.reviewer()
        folder, value = self.valuation()
        value['repair_scenarios'] = [{'basis': 'if_repaired', 'estimate': {
            'condition_basis': 'if_repaired', 'item_price_low': 100, 'item_price_high': 200,
            'currency': 'EUR'}, 'assumptions': ['Repaired'], 'evidence_refs': ['source.json'], 'missing_evidence': []}]
        q.write(folder / 'valuation.json', value)
        with self.assertRaisesRegex(ValueError, 'missing required schema'):
            q.complete_valuation(self.run, '111')

    def test_completed_output_tamper_is_visible_and_estimate_suppressed(self):
        self.prepared()
        self.reviewer()
        folder, _ = self.valuation()
        q.complete_valuation(self.run, '111')
        (folder / 'valuation.md').write_text('Tampered conclusion')
        report = q.report(self.run)
        self.assertEqual(report['cases'][0]['integrity_errors'], ['Completed valuation output changed'])
        self.assertIsNone(report['cases'][0]['estimate'])
        self.assertTrue((self.run / 'pilot-report.md').is_file())

    def test_reopen_correction_preserves_history_and_accepts_new_manifest(self):
        self.prepared()
        self.reviewer(candidates=False)
        folder = self.run / 'cases/111/reviewer'
        old_hash = q.sha(folder / 'READY.json')
        result = q.reopen_review(self.run, '111', 'agent-111', 'Correct sold-source provenance')
        self.assertEqual(result['correction_count'], 1)
        self.assertFalse(result['new_dispatch_required'])
        resumed = q.job(self.run, '111', 'reviewer')
        self.assertTrue(resumed['resume_only'])
        self.assertEqual(resumed['agent_id'], 'agent-111')
        with self.assertRaises(ValueError):
            q.job(self.run, '111', 'valuator')
        handoff = q.read(folder / 'handoff.json')
        handoff['research'] = {'correction': 'Source provenance corrected'}
        q.write(folder / 'handoff.json', handoff)
        finalize(folder)
        self.assertEqual(q.complete_review(self.run, '111')['stage'], 'unresolved')
        with q.session(self.run) as (run, db, _):
            _, data = q.get_case(db, run, '111')
            self.assertEqual(data['reviewer_completion_history'][0]['reviewer_ready_sha256'], old_hash)
            self.assertEqual(data['reviewer_ready_sha256'], q.sha(folder / 'READY.json'))
            self.assertNotEqual(old_hash, data['reviewer_ready_sha256'])
            self.assertEqual(data['reviewer_agent_id'], 'agent-111')
        self.assertEqual(q.report(self.run)['cases'][0]['integrity_errors'], [])

    def test_reopen_rejects_wrong_agent_and_noncompleted_review(self):
        self.prepared(('111', '222'))
        self.reviewer('111')
        with self.assertRaisesRegex(ValueError, 'match the recorded reviewer'):
            q.reopen_review(self.run, '111', 'different-agent', 'Wrong reviewer')
        q.stop(self.run, '222', 'Never reviewed')
        with self.assertRaisesRegex(ValueError, 'Only a completed review'):
            q.reopen_review(self.run, '222', 'agent-222', 'Not a completed review')

    def test_reopen_rejects_any_prepared_sol_job_in_run(self):
        self.prepared(('111', '222'))
        self.reviewer('111', candidates=False)
        self.reviewer('222')
        q.job(self.run, '222', 'valuator')
        with self.assertRaisesRegex(ValueError, 'after a Sol job'):
            q.reopen_review(self.run, '111', 'agent-111', 'Correction too late')

    def test_unresolved_completed_review_integrity_is_checked(self):
        self.prepared()
        self.reviewer(candidates=False)
        (self.run / 'cases/111/reviewer/source.json').write_text('Changed after completion')
        case = q.report(self.run)['cases'][0]
        self.assertEqual(case['stage'], 'unresolved')
        self.assertTrue(case['integrity_errors'])

    def test_reopened_review_blocks_other_sol_until_correction_finishes(self):
        self.prepared(('111', '222'))
        self.reviewer('111', candidates=False)
        self.reviewer('222')
        q.reopen_review(self.run, '111', 'agent-111', 'Correct source interpretation')
        with self.assertRaisesRegex(ValueError, 'All selected reviewers'):
            q.job(self.run, '222', 'valuator')
        finalize(self.run / 'cases/111/reviewer')
        q.complete_review(self.run, '111')
        self.assertFalse(q.job(self.run, '222', 'valuator')['resume_only'])

    def test_object_economic_outcome_fails_closed_leaves_dispatch_pending(self):
        self.prepared()
        self.reviewer()
        folder, value = self.valuation()
        value['economic_outcome'] = {'status': 'unresolved', 'reason': 'Target price is withheld'}
        value['comparisons'][0]['disposition'] = 'conditional'
        value['estimate'] = {'kind': 'conditional_target_estimate', 'currency': 'EUR',
            'condition_basis': 'current_condition', 'item_price_low': 80, 'item_price_high': 100,
            'central_estimate': 90, 'method': 'Synthetic transfer', 'source_sale_ids': ['sold-1'],
            'assumptions': ['Synthetic assumption'], 'limitations': ['Synthetic test']}
        q.write(folder / 'valuation.json', value)
        with self.assertRaisesRegex(ValueError, r'valuation.economic_outcome must be a string enum .*got dict'):
            q.complete_valuation(self.run, '111')
        with q.session(self.run) as (run, db, _):
            stage, data = q.get_case(db, run, '111')
            self.assertEqual(stage, 'valuator_dispatched')
            self.assertEqual(data['valuator_agent_id'], 'sol-111')
            self.assertNotIn('estimate', data)
            self.assertNotIn('valuation_files', data)
            self.assertNotIn('valuation_completed_at', data)
        self.assertIsNone(q.report(self.run)['cases'][0]['estimate'])
        self.assertIsInstance(q.read(folder / 'valuation.json')['economic_outcome'], dict)

    def test_unhashable_comparison_and_scenario_enums_report_field(self):
        self.prepared()
        self.reviewer()
        folder, baseline = self.valuation()
        malformed = [
            ('disposition', {'status': 'conditional'}, r'comparisons\[0\].disposition'),
            ('condition_basis', ['current_condition'], r'comparisons\[0\].condition_basis')]
        for field, bad_value, message in malformed:
            with self.subTest(field=field):
                value = json.loads(json.dumps(baseline))
                value['comparisons'][0][field] = bad_value
                q.write(folder / 'valuation.json', value)
                with self.assertRaisesRegex(ValueError, message + ' must be a string enum'):
                    q.complete_valuation(self.run, '111')
        value = json.loads(json.dumps(baseline))
        value['repair_scenarios'] = [{'basis': {'value': 'if_repaired'}, 'estimate': None,
                                     'assumptions': [], 'evidence_refs': [], 'missing_evidence': []}]
        q.write(folder / 'valuation.json', value)
        with self.assertRaisesRegex(ValueError, r'repair_scenarios\[0\].basis must be a string enum'):
            q.complete_valuation(self.run, '111')

    def test_object_handoff_outcome_rejected_with_actionable_message(self):
        self.prepared()
        self.reviewer(candidates=False)
        q.reopen_review(self.run, '111', 'agent-111', 'Synthetic schema correction test')
        folder = self.run / 'cases/111/reviewer'
        handoff = q.read(folder / 'handoff.json')
        handoff['outcome'] = {'status': 'unresolved'}
        q.write(folder / 'handoff.json', handoff)
        finalize(folder)
        with self.assertRaisesRegex(ValueError, 'handoff.outcome must be a string enum'):
            q.complete_review(self.run, '111')

    def test_later_run_claim_does_not_rewrite_earlier_discovery_disposition(self):
        self.discovery(self.run, ('111', '222'))
        before = q.report(self.run)
        # Run names and frozen times deliberately offer no chronological signal.
        later = self.new_run('alphabetically-earlier')
        self.discovery(later, ('111',))
        q.select(later, ['111'])
        after = q.report(self.run)
        self.assertEqual([row['disposition'] for row in before['sightings']],
                         [row['disposition'] for row in after['sightings']])
        row = after['sightings'][0]
        self.assertEqual(row['disposition'], 'eligible_not_selected')
        self.assertFalse(row['prior_external_claim_at_import'])
        self.assertEqual(row['claim_history_status'], 'event_order_verified')
        self.assertEqual(row['current_external_claim_run'], str(later.resolve()))

    def test_actual_prior_claim_uses_import_event_order(self):
        self.discovery(self.run, ('111',))
        q.select(self.run, ['111'])
        later = self.new_run('two')
        self.discovery(later, ('111',))
        row = q.report(later)['sightings'][0]
        self.assertEqual(row['disposition'], 'duplicate_claimed_prior_run')
        self.assertTrue(row['prior_external_claim_at_import'])
        self.assertEqual(row['claim_history_status'], 'event_order_verified')

    def test_claim_does_not_replace_ineligible_occurrence_status(self):
        self.discovery(self.run, ('111',))
        q.select(self.run, ['111'])
        later = self.new_run('two')
        self.discovery(later, (), [{'listing_id': '111', 'text': 'Heute, 15:00',
                                   'observed_at': '2026-09-12T13:05:00Z'}])
        row = q.report(later)['sightings'][0]
        self.assertEqual(row['status'], 'outside_window')
        self.assertEqual(row['disposition'], 'outside_window')
        self.assertTrue(row['prior_external_claim_at_import'])

    def test_missing_legacy_event_order_is_explicitly_unknown(self):
        self.discovery(self.run, ('111',))
        q.select(self.run, ['111'])
        later = self.new_run('two')
        self.discovery(later, ('111',))
        with q.session(self.run) as (run, db, _):
            db.execute('DELETE FROM events WHERE run=? AND action="selected"', (str(run),))
        row = q.report(later)['sightings'][0]
        self.assertEqual(row['disposition'], 'eligible_claim_history_unresolved')
        self.assertIsNone(row['prior_external_claim_at_import'])
        self.assertEqual(row['claim_history_status'], 'event_order_missing')

    def test_import_prefers_explicit_posting_metadata_including_missing(self):
        rows = []
        for identity, label in [('111', '10.09.2026'), ('222', None), ('333', ''), ('444', 'Heute, 13:50')]:
            rows.append({'listing_id': identity, 'text': 'Title says Heute, 13:50 and model from 10.09.2026',
                         'posting_label': label, 'observed_at': '2026-09-12T12:10:00Z'})
        self.discovery(self.run, (), rows)
        statuses = {row['listing_id']: row['status'] for row in q.report(self.run)['sightings']}
        self.assertEqual(statuses, {'111': 'outside_window', '222': 'unresolved',
                                    '333': 'unresolved', '444': 'eligible'})
        for identity in ('111', '222', '333'):
            with self.assertRaisesRegex(ValueError, 'no eligible'):
                q.select(self.run, [identity])
        q.select(self.run, ['444'])

    def test_legacy_import_status_is_not_reinterpreted_by_reporting(self):
        self.discovery(self.run, (), [{'listing_id': '111', 'text': '10.09.2026',
                                      'observed_at': '2026-09-12T12:10:00Z'}])
        before = q.report(self.run)['sightings'][0]
        self.assertEqual(before['status'], 'unresolved')
        # Retaining a better new input alongside the old import cannot mutate it.
        self.discovery(self.run, (), [{'listing_id': '111', 'text': '10.09.2026', 'posting_label': '10.09.2026',
                                      'observed_at': '2026-09-12T12:10:00Z'}])
        sightings = q.report(self.run)['sightings']
        old = next(row for row in sightings if row['digest'] == before['digest'])
        self.assertEqual(old['status'], 'unresolved')
        self.assertEqual(sorted(row['status'] for row in sightings), ['outside_window', 'unresolved'])

    def test_direct_capture_cli_opt_in_is_frozen(self):
        run = self.root / 'cli-direct'
        with redirect_stdout(io.StringIO()):
            self.assertEqual(q.main(['init', str(run), '--state', str(self.db),
                             '--workflow-version', '1', '--require-direct-research-capture']), 0)
        manifest = q.read(run / 'manifest.json')
        self.assertTrue(manifest['require_direct_research_capture'])
        manifest['require_direct_research_capture'] = False
        q.write(run / 'manifest.json', manifest)
        with self.assertRaisesRegex(ValueError, 'Frozen manifest changed'):
            q.report(run)

    def test_legacy_manifest_without_capture_flag_keeps_old_contract(self):
        # Simulate an existing run with the original manifest in both frozen stores.
        manifest = q.read(self.run / 'manifest.json')
        del manifest['require_direct_research_capture']
        with closing(q.connect(self.db)) as db:
            with db:
                db.execute('UPDATE runs SET manifest=? WHERE run=?', (json.dumps(manifest), str(self.run.resolve())))
        q.write(self.run / 'manifest.json', manifest)
        self.prepared()
        self.assertEqual(self.reviewer()['stage'], 'review_done')

    def test_direct_capture_missing_packets_blocks_unresolved_completion(self):
        self.direct_prepared()
        self.reviewer(candidates=False, complete=False)
        with self.assertRaisesRegex(ValueError, 'capture_packets must be an array'):
            q.complete_review(self.run, '111')
        with q.session(self.run) as (run, db, _):
            stage, data = q.get_case(db, run, '111')
            self.assertEqual(stage, 'reviewer_dispatched')
            self.assertNotIn('reviewer_ready_sha256', data)

    def test_direct_capture_identity_stop_needs_explicit_reason_and_no_rows(self):
        self.direct_prepared()
        folder = self.reviewer(candidates=False, complete=False)
        handoff = q.read(folder / 'handoff.json')
        for research in ({'capture_packets': []}, {'capture_packets': [], 'attempted': True, 'reason': 'Unknown model'},
                         {'capture_packets': [], 'attempted': False, 'reason': ' '}):
            handoff['research'] = research
            q.write(folder / 'handoff.json', handoff)
            finalize(folder)
            with self.assertRaisesRegex(ValueError, 'empty capture_packets requires'):
                q.complete_review(self.run, '111')
        handoff['research'] = {'capture_packets': [], 'attempted': False, 'reason': 'Target model cannot be identified'}
        handoff['grouped_uninspected_rows'] = [{'title': 'Claimed source row'}]
        q.write(folder / 'handoff.json', handoff)
        finalize(folder)
        with self.assertRaisesRegex(ValueError, 'empty capture_packets requires'):
            q.complete_review(self.run, '111')
        handoff['grouped_uninspected_rows'] = []
        q.write(folder / 'handoff.json', handoff)
        finalize(folder)
        self.assertEqual(q.complete_review(self.run, '111')['stage'], 'unresolved')
        with q.session(self.run) as (run, db, _):
            _, data = q.get_case(db, run, '111')
            self.assertEqual(data['reviewer_check']['direct_research_capture']['status'], 'not_attempted')
        with self.assertRaises(ValueError):
            q.job(self.run, '111', 'valuator')

    def test_direct_capture_missing_or_changed_raw_blocks_even_with_fresh_ready(self):
        self.direct_prepared()
        folder = self.reviewer(complete=False)
        capture = self.direct_research(folder)
        raw = capture / 'dom.txt'
        original = raw.read_bytes()
        raw.unlink()
        finalize(folder)
        with self.assertRaisesRegex(ValueError, 'Missing raw file'):
            q.complete_review(self.run, '111')
        raw.write_bytes(b'X' * len(original))
        finalize(folder)
        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
            q.complete_review(self.run, '111')
        raw.write_bytes(original)
        finalize(folder)
        self.assertEqual(q.complete_review(self.run, '111')['stage'], 'review_done')
        self.assertEqual(q.report(self.run)['cases'][0]['outcome'], 'unresolved')

    def test_direct_capture_escaped_packet_and_attempt_paths_block(self):
        self.direct_prepared()
        folder = self.reviewer(complete=False)
        self.direct_research(folder)
        handoff = q.read(folder / 'handoff.json')
        for escaped in ('../../outside/packet.json', str((self.root / 'outside.json').resolve())):
            handoff['research']['capture_packets'] = [escaped]
            q.write(folder / 'handoff.json', handoff)
            finalize(folder)
            with self.assertRaisesRegex(ValueError, 'Direct research capture:'):
                q.complete_review(self.run, '111')
        handoff['research']['capture_packets'] = ['research/packet.json']
        q.write(folder / 'handoff.json', handoff)
        packet = q.read(folder / 'research/packet.json')
        packet['attempts'][0]['capture_path'] = '../../outside/capture.json'
        q.write(folder / 'research/packet.json', packet)
        finalize(folder)
        with self.assertRaisesRegex(ValueError, 'Path escapes root'):
            q.complete_review(self.run, '111')

    def test_direct_capture_wrong_case_and_missing_ready_coverage_block(self):
        self.direct_prepared()
        folder = self.reviewer(complete=False)
        capture = self.direct_research(folder, identity='222')
        with self.assertRaisesRegex(ValueError, 'case_id does not match'):
            q.complete_review(self.run, '111')
        packet = q.read(folder / 'research/packet.json')
        packet['case_id'] = '111'
        q.write(folder / 'research/packet.json', packet)
        # lessons.md is exempt from the general READY coverage check; research raw
        # evidence still must be explicitly covered even with this filename.
        (capture / 'dom.txt').rename(capture / 'lessons.md')
        manifest = q.read(capture / 'capture.json')
        manifest['files']['dom_snapshot']['path'] = 'lessons.md'
        q.write(capture / 'capture.json', manifest)
        finalize(folder)
        with self.assertRaisesRegex(ValueError, 'omitted from READY manifest'):
            q.complete_review(self.run, '111')
        ready = q.read(folder / 'READY.json')
        ready['files'].append({'path': 'research/search-01/lessons.md', 'sha256': q.sha(capture / 'lessons.md')})
        q.write(folder / 'READY.json', ready)
        self.assertEqual(q.complete_review(self.run, '111')['stage'], 'review_done')

    def test_direct_capture_tamper_blocks_report_and_later_sol_dispatch(self):
        self.direct_prepared(('111', '222'))
        first = self.reviewer('111', complete=False)
        capture = self.direct_research(first)
        q.complete_review(self.run, '111')
        second = self.reviewer('222', complete=False)
        self.direct_research(second, identity='222')
        q.complete_review(self.run, '222')
        q.job(self.run, '222', 'valuator')
        (capture / 'rendered.txt').write_text('Changed retained evidence', encoding='utf-8')
        self.assertTrue(q.report(self.run)['cases'][0]['integrity_errors'])
        with self.assertRaisesRegex(ValueError, 'Handoff check failed'):
            q.job(self.run, '111', 'valuator')
        # Sol's own prepared input is unchanged; the run-wide barrier must still
        # reject changed evidence belonging to the other completed reviewer.
        with self.assertRaisesRegex(ValueError, 'Handoff check failed'):
            q.dispatched(self.run, '222', 'valuator', 'sol-222')


if __name__ == '__main__':
    unittest.main()
