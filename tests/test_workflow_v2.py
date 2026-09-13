"""Offline workflow v2 integration: no browser, model dispatch or real listings."""
import copy
from pathlib import Path
import unittest

from evaluation.handoff import finalize
from evaluation.gates import evaluate
from pilot import queue as q
from helpers import WorkflowTwoFixture, EconomicFixture


class WorkflowTwoTests(WorkflowTwoFixture, unittest.TestCase):


    def test_default_is_generic_and_no_platform_or_recipe_in_job(self):
        self.prepared()
        job = q.job(self.run, '111', 'reviewer')['job']
        for prohibited in ('eBay', 'Product Research', 'three captured result states', 'sale IDs'):
            self.assertNotIn(prohibited, job['prompt'])
        self.assertEqual(q.read(self.run / 'manifest.json')['workflow_version'], 2)
        self.assertEqual(q.read(self.run / 'manifest.json')['sampled_category'], 'future_category')

    def test_zero_evidence_reaches_valuator_and_completes_abstention(self):
        self.prepared()
        self.assertEqual(self.reviewer(evidence=False)['stage'], 'review_done')
        self.valuation(evidence=False)
        self.assertEqual(q.complete_valuation(self.run, '111')['outcome'], 'unresolved')
        self.assertFalse(q.report(self.run)['cases'][0]['integrity_errors'])

    def test_modelled_only_can_get_conditional_estimate_but_not_economic_support(self):
        self.prepared()
        self.reviewer()
        self.valuation(estimate=True)
        result = q.complete_valuation(self.run, '111')
        self.assertEqual(result['outcome'], 'unresolved')
        self.assertFalse(result['purchase_authorized'])

    def test_adding_candidates_during_research_then_freezing_at_valuation(self):
        self.prepared()
        self.reviewer()
        self.discovery(self.run, ('222', '333'))
        q.select(self.run, ['222'])
        with self.assertRaisesRegex(ValueError, 'terminal'):
            q.job(self.run, '111', 'valuator')
        q.stop(self.run, '222', 'Fixture budget exhausted before capture', 'skipped')
        q.job(self.run, '111', 'valuator')
        with self.assertRaisesRegex(ValueError, 'Selection froze'):
            q.select(self.run, ['333'])

    def test_deferred_retained_without_claim_and_rejection_not_economic_outcome(self):
        self.discovery(self.run, ('111', '222', '333'))
        q.triage(self.run, '111', 'deferred', 'Promising; research budget reserved for another case')
        q.triage(self.run, '222', 'rejected', 'Listing explicitly lacks the required component; see rows.json')
        report = q.report(self.run)
        self.assertEqual([r['disposition'] for r in report['sightings']],
                         ['deferred', 'rejected', 'eligible_not_selected'])
        self.assertEqual(report['cases'], [])
        self.assertEqual([r['listing_id'] for r in q.backlog(self.db)['deferred']], ['111'])
        q.select(self.run, ['111'])
        self.assertEqual(q.backlog(self.db)['deferred'], [])
        self.assertEqual(q.report(self.run)['sightings'][0]['disposition'], 'selected')

    def test_carryover_preserves_origin_and_never_changes_new_discovery_window(self):
        self.discovery(self.run)
        q.triage(self.run, '111', 'deferred', 'Research capacity exhausted')
        later = self.root / 'later'
        q.init(later, self.db, '2026-09-13T12:00:30Z', self.playbooks)
        with self.assertRaisesRegex(ValueError, 'no eligible time evidence'):
            q.select(later, ['111'])
        q.select(later, ['111'], carryover=True)
        report = q.report(later)
        self.assertEqual(report['row_occurrences'], 0)
        self.assertEqual(report['cases'][0]['carryover_from_run'], str(self.run.resolve()))
        self.assertEqual(report['manifest']['window_start_inclusive'], '2026-09-12T12:00:30+00:00')
        self.assertEqual(q.backlog(self.db)['deferred'], [])
        with self.assertRaises(ValueError):
            q.select(later, ['999'], carryover=True)

    def test_generic_workflow_cannot_silently_ignore_legacy_capture_requirement(self):
        with self.assertRaisesRegex(ValueError, 'legacy-only'):
            q.init(self.root / 'invalid', self.db, playbook_root=self.playbooks,
                   require_direct_research_capture=True)

    def test_missing_raw_evidence_rejected_even_with_rebuilt_hashes(self):
        self.prepared()
        folder = self.reviewer(complete=False)
        handoff = q.read(folder / 'handoff.json')
        handoff['evidence'][0]['source_paths'] = ['missing.txt']
        q.write(folder / 'handoff.json', handoff)
        finalize(folder)
        with self.assertRaisesRegex(ValueError, 'Evidence reference absent'):
            q.complete_review(self.run, '111')

    def test_tampering_accepted_original_blocks_valuation(self):
        self.prepared()
        self.reviewer()
        (self.run / 'cases/111/reviewer/raw.txt').write_text('changed', encoding='utf-8')
        with self.assertRaises(ValueError):
            q.job(self.run, '111', 'valuator')

    def test_kind_promotion_and_invalid_estimate_references_rejected(self):
        self.prepared()
        self.reviewer()
        folder, value = self.valuation(estimate=True)
        for mutate in ('kind', 'refs', 'estimate', 'adequacy'):
            bad = copy.deepcopy(value)
            if mutate == 'kind':
                bad['comparisons'][0]['kind'] = 'transaction'
            elif mutate == 'refs':
                bad['comparisons'][0]['evidence_refs'] = ['../outside.txt']
            elif mutate == 'estimate':
                bad['estimate']['source_evidence_ids'] = ['nonexistent']
            else:
                bad['evidence_assessment']['status'] = 'insufficient'
            q.write(folder / 'valuation.json', bad)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                q.complete_valuation(self.run, '111')

    def test_scope_exclusion_needs_retained_evidence(self):
        self.prepared()
        folder = self.reviewer(complete=False)
        value = q.read(folder / 'handoff.json')
        value['outcome'] = 'unsupported'
        q.write(folder / 'handoff.json', value)
        finalize(folder)
        with self.assertRaisesRegex(ValueError, 'scope exclusion'):
            q.complete_review(self.run, '111')
        value['scope_exclusion'] = {'reason': 'Synthetic wanted ad', 'source_paths': ['target/input.json']}
        q.write(folder / 'handoff.json', value)
        finalize(folder)
        q.complete_review(self.run, '111')
        self.assertEqual(q.report(self.run)['cases'][0]['outcome'], 'unsupported')


class EconomicTwoTests(EconomicFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.review['schema_version'] = 2
        self.review['comps'] = self.review['comps'][:2]
        for comp in self.review['comps']:
            comp['evidence_id'] = comp.pop('sale_id')
            comp.update(kind='transaction', condition_basis='current_condition', locator='synthetic:transaction/' + comp['evidence_id'],
                        source_paths=['raw.txt'], captured_at='2026-09-12T12:00:00Z')
        self.review['transaction_adequacy'] = {'status': 'sufficient', 'reason': 'Synthetic same-condition transactions',
                                               'evidence_ids': ['0', '1'], 'limitations': ['Small synthetic sample']}

    def test_two_audited_transactions_with_explicit_adequacy_can_support(self):
        self.assertEqual(evaluate(self.record, self.review)['outcome'], 'supported')

    def test_count_cannot_replace_adequacy(self):
        del self.review['transaction_adequacy']
        self.assertEqual(evaluate(self.record, self.review)['outcome'], 'unresolved')

    def test_nontransaction_or_duplicate_evidence_cannot_support(self):
        for mutation in ({'kind': 'modelled_estimate'}, {'price_basis': 'asking_price'}, {'evidence_id': '0'}):
            review = copy.deepcopy(self.review)
            review['comps'][1].update(mutation)
            with self.subTest(mutation=mutation):
                self.assertEqual(evaluate(self.record, review)['outcome'], 'unresolved')

    def test_unknown_costs_still_block(self):
        self.review['costs']['fees']['eur'] = None
        self.assertEqual(evaluate(self.record, self.review)['outcome'], 'unresolved')
