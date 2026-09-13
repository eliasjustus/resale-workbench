import unittest

from evaluation.gates import evaluate
from evaluation.repair import validate_repair


def repair_packet(status='unresolved'):
    return {
        'schema_version': 1, 'status': status,
        'symptoms': [{'claim': 'Does not boot', 'source': 'seller',
                      'evidence_refs': ['target.json:description']}],
        'hypotheses': [{'cause': 'Storage or motherboard fault', 'evidence_refs': []}],
        'diagnostics': [{'test': 'Boot known-good external media',
                         'distinguishes': ['installed OS failure', 'broader hardware fault'],
                         'equipment': ['compatible boot media'], 'availability': 'unknown'}],
        'diagnosis_evidence': [], 'verification_evidence': [],
        'downside': {'status': 'unresolved', 'evidence_refs': []},
        'unknowns': ['Cause and functional state of individual parts']}


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.record = {'listing_id': '1', 'capture_id': 'a', 'collection_status': 'complete',
                       'fields': {'asking_price': {'value': '50 €'}}}
        self.review = {
            'listing_id': '1', 'capture_id': 'a', 'as_of': '2026-09-12',
            'scope': {'status': 'included', 'evidence': ['target']},
            'work': {'required': True, 'evidence': ['no boot'], 'repair_materials_eur': '10',
                     'additional_profit_eur': '20', 'basis': 'synthetic explicit work allowance'},
            'acquisition_price': {'eur': '50', 'source_text': '50 €'},
            'costs': {name: {'eur': '2', 'basis': 'synthetic cost'} for name in (
                'acquisition_transport', 'resale_shipping', 'fees', 'packaging', 'defect_allowance')},
            'comps': [{'sale_id': str(i), 'units_sold': 1, 'review_status': 'accepted',
                       'review_evidence': ['synthetic audit'], 'condition_basis': 'current_condition',
                       'price_basis': 'actual_sold_item_price', 'currency': 'EUR',
                       'seller_country': 'DE', 'sold_at': '2026-09-01', 'item_price_eur': '200'}
                      for i in range(5)]}
        for name in ('identity', 'condition', 'gallery'):
            self.review[name] = {'status': 'resolved', 'evidence': ['synthetic fixture']}

    def assert_unresolved(self):
        result = evaluate(self.record, self.review)
        self.assertEqual(result['outcome'], 'unresolved')
        self.assertIsNone(result['arithmetic'])
        self.assertFalse(result['purchase_authorized'])
        return result

    def test_allowance_and_large_working_sale_prices_cannot_resolve_no_boot(self):
        self.assertIn('repair_contract_missing', self.assert_unresolved()['reasons'])
        self.review['repair'] = repair_packet()
        self.assertEqual(validate_repair(self.review['repair']), [])
        self.assert_unresolved()
        # Even an evidenced diagnosis is not evidence the planned repair succeeds.
        self.review['repair'].update(status='diagnosed', diagnosis_evidence=['diagnostic.log'])
        self.assertEqual(validate_repair(self.review['repair']), [])
        self.assert_unresolved()

    def test_malformed_or_probability_augmented_contract_never_passes(self):
        for bad in (None, [], {}, {'schema_version': True},
                    {**repair_packet(), 'success_probability': 0.95},
                    {**repair_packet(), 'symptoms': ['probably Windows']},
                    {**repair_packet(), 'diagnostics': [{'test': 'try a PSU'}]},
                    {**repair_packet(), 'downside': {'status': 'evidenced', 'evidence_refs': []}}):
            with self.subTest(packet=bad):
                self.assertTrue(validate_repair(bad))
                self.review['repair'] = bad
                self.assert_unresolved()

    def test_claimed_completed_repair_needs_verification_and_downside_evidence(self):
        repair = repair_packet('repaired_verified')
        self.review['repair'] = repair
        self.assert_unresolved()
        repair['diagnosis_evidence'] = ['diagnostic.log']
        repair['verification_evidence'] = ['current-capture/function-test.log']
        self.assert_unresolved()
        repair['downside'] = {'status': 'evidenced', 'evidence_refs': ['current-capture/condition.log']}
        result = evaluate(self.record, self.review)
        self.assertEqual(result['outcome'], 'supported')
        self.assertFalse(result['purchase_authorized'])
        # Passing repair structure cannot replace five distinct current-condition sales.
        self.review['comps'][4]['condition_basis'] = 'if_repaired'
        self.assert_unresolved()

    def test_scenario_values_and_untested_parts_do_not_become_current_value(self):
        self.review['repair'] = repair_packet()
        self.review['repair_scenarios'] = [{'basis': 'if_repaired', 'estimate': {'item_price_low': 1000}}]
        self.assert_unresolved()
        # Explicit scenario comparables are rejected even on a legacy no-work review.
        del self.review['repair']
        del self.review['repair_scenarios']
        self.review['work'] = {'required': False, 'evidence': ['synthetic tested item']}
        for basis in ('if_repaired', 'parts_out'):
            self.review['comps'][0]['condition_basis'] = basis
            result = self.assert_unresolved()
            self.assertIn('not_current_condition_comparable', result['excluded_comps'][0]['reasons'])

    def test_unknown_equipment_is_allowed_but_not_silently_available(self):
        packet = repair_packet()
        self.assertEqual(validate_repair(packet), [])
        packet['diagnostics'][0]['availability'] = True
        self.assertTrue(validate_repair(packet))


if __name__ == '__main__':
    unittest.main()
