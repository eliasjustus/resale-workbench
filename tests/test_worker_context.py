"""Ensure frozen research criteria reach both isolated roles without price anchors."""
import json
import unittest

from pilot import queue as q
from helpers import ConfiguredPilotFixture, build_v2_review


class WorkerResearchContextTests(ConfiguredPilotFixture, unittest.TestCase):


    def finish_synthetic_review(self):
        folder = build_v2_review(self, evidence=False, complete=False)
        ready = q.read(folder / 'READY.json')
        ready['completed_at_utc'] = q.now()
        q.write(folder / 'READY.json', ready)
        q.complete_review(self.run, '111')

    def test_both_roles_receive_frozen_market_recency_and_category_without_profit(self):
        self.config['search']['categories'] = ['synthetic_component_scope']
        self.config['economics'].update(minimum_profit_eur='73.19', sold_max_age_days=7)
        self.run = self.root / 'context-v2'
        q.init(self.run, start='2026-09-12T12:00:30Z', playbook_root=self.playbooks,
               config=self.config, category='synthetic_component_scope')
        self.prepared()
        reviewer = q.job(self.run, '111', 'reviewer')['job']
        # The next role must use frozen run settings, not an edited source config.
        self.config['economics']['sold_max_age_days'] = 365
        self.config['search']['categories'] = ['edited_after_initialization']
        self.finish_synthetic_review()
        valuator = q.job(self.run, '111', 'valuator')['job']
        expected = {'country': 'DE', 'currency': 'EUR', 'sold_max_age_days': 7,
                    'sampled_category': 'synthetic_component_scope'}
        for role, job in [('reviewer', reviewer), ('valuator', valuator)]:
            with self.subTest(role=role):
                self.assertEqual(job['research_context'], expected)
                self.assertIn(json.dumps(expected), job['prompt'])
                self.assertIn('DE (Germany)', job['prompt'])
                self.assertIn('later economic review as_of date', job['prompt'])
                self.assertIn('not evidence of the object identity', job['prompt'])
                self.assertNotIn('minimum_profit_eur', json.dumps(job))
                self.assertNotIn('73.19', job['prompt'])
                self.assertNotIn('acquisition_price', json.dumps(job['research_context']))
                self.assertEqual(q.read(self.run / f'jobs/111-{role}.json')['research_context'], expected)

    def test_legacy_unconfigured_run_uses_historical_research_policy(self):
        self.run = self.root / 'one'
        self.prepared()
        job = q.job(self.run, '111', 'reviewer')['job']
        self.assertEqual(job['research_context'], {'country': 'DE', 'currency': 'EUR',
                         'sold_max_age_days': 180, 'sampled_category': None})
        self.assertEqual(job['model'], 'gpt-5.6-luna')
        self.assertIn('three captured result states', job['prompt'])


if __name__ == '__main__':
    unittest.main()
