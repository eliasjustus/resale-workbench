"""Synthetic configuration, economics and preparation-budget integration tests."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

from pilot import queue as q
from pilot.config import default_data_dir, economic_policy, example_config, load_config, validate_config
from evaluation.gates import evaluate
from helpers import ConfiguredPilotFixture, EconomicFixture


class ConfigurationTests(ConfiguredPilotFixture, unittest.TestCase):


    def test_frozen_config_and_relative_state_are_independent_of_source(self):
        config_path = self.root / 'settings.toml'
        config_path.write_text(example_config(), encoding='utf-8')
        loaded = load_config(config_path)
        self.assertEqual(loaded['data_dir'], str(self.root / 'private-data'))
        self.assertIsNone(loaded['budget']['max_cases'])
        self.assertIsNone(loaded['budget']['reviewer_job_limit'])
        self.assertEqual(q.read(self.run / 'pilot.json')['state'], str(self.root / 'user-data/pilot-state.sqlite3'))
        self.config['roles']['valuator']['model'] = 'changed-later'
        manifest = q.report(self.run)['manifest']
        self.assertEqual(manifest['configuration']['roles']['valuator']['model'], 'test-local-model')
        self.assertEqual(manifest['center'], 'Berlin')
        self.assertEqual(manifest['radius_km'], 50)
        self.assertIn('Berlin', (self.run / 'pilot-report.md').read_text())
        manifest['configuration']['economics']['minimum_profit_eur'] = '0'
        q.write(self.run / 'manifest.json', manifest)
        with self.assertRaisesRegex(ValueError, 'Frozen manifest changed'):
            q.report(self.run)

    def test_config_rejects_unknown_mistyped_or_unsupported_settings(self):
        bad_values = [({'unexpected': True}), ({'search': {'center': ''}}),
                      ({'economics': {'currency': 'USD'}}), ({'economics': {'minimum_profit_eur': 'NaN'}}),
                      ({'budget': {'max_cases': True}}), ({'budget': {'preparation_minutes': 0}}),
                      ({'roles': {'reviewer': {'model': 'x', 'reasoning_effort': []}}}),
                      ({'capabilities': {'notes': 'tools'}})]
        for change in bad_values:
            with self.subTest(change=change):
                config = copy.deepcopy(self.config)
                config.update(change)
                with self.assertRaises(ValueError):
                    validate_config(config)
        config = copy.deepcopy(self.config)
        config['search']['categories'] *= 2
        with self.assertRaisesRegex(ValueError, 'unique'):
            validate_config(config)

    def test_category_and_default_data_location(self):
        with self.assertRaisesRegex(ValueError, 'Sampled category'):
            q.init(self.root / 'bad-category', config=self.config, category='unconfigured')
        self.assertFalse(default_data_dir().resolve().is_relative_to(Path(q.__file__).resolve().parents[1]))
        legacy = q.read(self.root / 'one/manifest.json')
        self.assertIsNone(legacy['center'])
        self.assertEqual(legacy['radius_km'], 100)

    def test_selection_limit_and_post_deadline_closeout(self):
        self.discovery(self.run, ('111', '222', '333', '444'))
        with self.assertRaisesRegex(ValueError, 'max_cases'):
            q.select(self.run, ['111', '222', '333', '444'])
        self.assertFalse(q.report(self.run)['cases'])
        q.select(self.run, ['111'])
        with patch.object(q, 'now', return_value='2026-09-12T13:01:00+00:00'):
            with self.assertRaisesRegex(ValueError, 'deadline'):
                q.select(self.run, ['222'])
            self.assertEqual(q.stop(self.run, '111', 'Synthetic deadline reached')['outcome'], 'unresolved')

    def test_default_preparation_window_has_no_listing_count_cutoff(self):
        config = copy.deepcopy(self.config)
        config['budget'] = {'preparation_minutes': 30}
        self.run = self.root / 'no-count-cutoff'
        q.init(self.run, start='2026-09-12T12:00:30Z', playbook_root=self.playbooks, config=config)
        identities = ['111', '222', '333', '444']
        self.discovery(self.run, identities)
        self.assertEqual(q.select(self.run, identities)['selected'], identities)

    def test_review_limit_and_reserved_valuation_after_deadline(self):
        self.prepared()
        folder = self.reviewer(complete=False)
        ready = q.read(folder / 'READY.json')
        ready['completed_at_utc'] = q.now()
        q.write(folder / 'READY.json', ready)
        q.complete_review(self.run, '111')
        with patch.object(q, 'now', return_value='2026-09-12T13:01:00+00:00'):
            folder, value = self.valuation()
            self.assertEqual(q.read(self.run / 'jobs/111-valuator.json')['model'], 'test-local-model')
            with self.assertRaisesRegex(ValueError, 'model/effort mismatch'):
                q.complete_valuation(self.run, '111')
            value.update(model_requested='test-local-model', effort_requested='medium')
            q.write(folder / 'valuation.json', value)
            self.assertEqual(q.complete_valuation(self.run, '111')['outcome'], 'unresolved')

    def test_new_review_job_blocked_after_deadline(self):
        self.prepared()
        with patch.object(q, 'now', return_value='2026-09-12T13:01:00+00:00'):
            with self.assertRaisesRegex(ValueError, 'deadline'):
                q.job(self.run, '111', 'reviewer')
            self.assertEqual(q.stop(self.run, '111', 'Preparation closed')['outcome'], 'unresolved')

    def test_reviewer_job_limit_preserves_existing_jobs(self):
        config = copy.deepcopy(self.config)
        config['budget']['reviewer_job_limit'] = 1
        self.run = self.root / 'limited-jobs'
        q.init(self.run, start='2026-09-12T12:00:30Z', playbook_root=self.playbooks, config=config)
        self.discovery(self.run, ('111', '222'))
        with self.assertRaisesRegex(ValueError, 'reviewer_job_limit'):
            q.select(self.run, ['111', '222'])
        self.prepared()
        q.job(self.run, '111', 'reviewer')
        with patch.object(q, 'now', return_value='2026-09-12T13:01:00+00:00'):
            self.assertTrue(q.job(self.run, '111', 'reviewer')['resume_only'])
            self.assertEqual(q.dispatched(self.run, '111', 'reviewer', 'past-external-job')['agent_id'], 'past-external-job')


class EconomicConfigurationTests(EconomicFixture, unittest.TestCase):

    def test_margin_policy_changes_arithmetic_without_relaxing_evidence(self):
        result = evaluate(self.record, self.review, {'minimum_profit_eur': '21'})
        self.assertEqual(result['outcome'], 'unsupported')
        self.assertEqual(result['arithmetic']['required_profit_eur'], '21.00')
        self.assertEqual(result['economic_policy']['currency'], 'EUR')
        self.review['comps'].pop()
        self.assertEqual(evaluate(self.record, self.review, {'minimum_profit_eur': '0'})['outcome'], 'unresolved')

    def test_recency_policy_is_enforced_and_retained(self):
        result = evaluate(self.record, self.review, {'sold_max_age_days': 10})
        self.assertEqual(result['outcome'], 'unresolved')
        self.assertIn('outside_preceding_10_days', result['excluded_comps'][0]['reasons'])
        with self.assertRaisesRegex(ValueError, 'Only Germany'):
            evaluate(self.record, self.review, {'country': 'US'})

    def test_equal_monetary_policies_have_identical_frozen_values(self):
        for values in (('20', '20.00', 20, 20.0), ('0', '0.00', '-0.00'), ('20.50', '20.5', 20.5)):
            policies = [economic_policy({'minimum_profit_eur': value}) for value in values]
            self.assertTrue(all(policy == policies[0] for policy in policies))

    def test_schema_two_rejects_malformed_transaction_provenance(self):
        from resale_tool.demo import sample
        record, review = sample('synthetic-contract', 50)
        mutations = [
            {'source_paths': 'not-a-list'}, {'source_paths': []}, {'source_paths': ['']},
            {'source_paths': [123]}, {'source_paths': [None]},
            {'locator': 123}, {'locator': ['synthetic:source']}, {'locator': '  '},
            {'captured_at': 'not-a-date'}, {'captured_at': '2026-01-15'},
            {'captured_at': '2026-01-15T12:00:00'}, {'captured_at': 123},
            {'captured_at': ['2026-01-15T12:00:00Z']},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(review)
                for comp in changed['comps']:
                    comp.update(mutation)
                result = evaluate(record, changed)
                self.assertEqual(result['outcome'], 'unresolved')
                self.assertFalse(result['accepted_comps'])
                self.assertIn('transaction_provenance_missing', result['excluded_comps'][0]['reasons'])
        self.assertEqual(evaluate(record, review)['outcome'], 'supported')


if __name__ == '__main__':
    unittest.main()
