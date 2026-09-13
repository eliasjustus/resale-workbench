import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from evaluation.comps import normalize_research
from evaluation.gates import evaluate
from helpers import EconomicFixture
from evaluation.packet import export_packet, mask_text


class EvaluationTests(EconomicFixture, unittest.TestCase):

    def test_positive_and_negative_margin_are_conditional_only(self):
        result = evaluate(self.record, self.review)
        self.assertEqual(result['arithmetic']['margin_eur'], '20.00')
        self.assertEqual(result['outcome'], 'supported')
        self.assertFalse(result['purchase_authorized'])
        self.review['costs']['defect_allowance']['eur'] = '22'
        self.assertEqual(evaluate(self.record, self.review)['outcome'], 'unsupported')

    def test_duplicate_or_aggregate_does_not_create_fifth_comp(self):
        for mutation in ({'sale_id': '0'}, {'units_sold': 3}, {'sale_id': None}):
            with self.subTest(mutation=mutation):
                review = copy.deepcopy(self.review)
                review['comps'][4].update(mutation)
                result = evaluate(self.record, review)
                self.assertEqual(result['outcome'], 'unresolved')
                self.assertEqual(len(result['accepted_comps']), 4)

    def test_twenty_euro_boundary_and_work_allowance(self):
        self.assertEqual(evaluate(self.record, self.review)['outcome'], 'supported')
        self.review['costs']['fees']['eur'] = '2.01'
        self.assertEqual(evaluate(self.record, self.review)['outcome'], 'unsupported')
        self.review['costs']['fees']['eur'] = '2'
        self.review['work'] = {'required': True, 'evidence': ['cosmetic cleaning needed']}
        self.assertEqual(evaluate(self.record, self.review)['outcome'], 'unresolved')
        self.review['work'].update(repair_materials_eur='5', additional_profit_eur='10', basis='explicit test allowance')
        for comp in self.review['comps']:
            comp['item_price_eur'] = '95'
            comp['condition_basis'] = 'current_condition'
        self.assertEqual(evaluate(self.record, self.review)['outcome'], 'unresolved')
        self.review['repair'] = {
            'schema_version': 1, 'status': 'not_needed', 'symptoms': [],
            'hypotheses': [], 'diagnostics': [], 'diagnosis_evidence': [],
            'verification_evidence': ['synthetic function test; only cleaning needed'],
            'downside': {'status': 'unresolved', 'evidence_refs': []}, 'unknowns': []}
        result = evaluate(self.record, self.review)
        self.assertEqual(result['outcome'], 'supported')
        self.assertEqual(result['arithmetic']['required_profit_eur'], '30.00')
        self.assertEqual(result['arithmetic']['margin_eur'], '30.00')
        self.review['work']['additional_profit_eur'] = '0'
        self.assertEqual(evaluate(self.record, self.review)['outcome'], 'unresolved')

    def test_unknown_work_requirement_abstains(self):
        del self.review['work']
        self.assertEqual(evaluate(self.record, self.review)['outcome'], 'unresolved')

    def test_future_old_target_and_unknown_country_comps_excluded(self):
        for mutation in ({'sold_at': '2026-09-13'}, {'sold_at': '2025-01-01'},
                         {'seller_country': None}, {'price_basis': 'asking_price'}):
            with self.subTest(mutation=mutation):
                review = copy.deepcopy(self.review)
                review['comps'][4].update(mutation)
                self.assertEqual(evaluate(self.record, review)['outcome'], 'unresolved')
        self.review['target_sale_id'] = '4'
        self.assertEqual(len(evaluate(self.record, self.review)['accepted_comps']), 4)

    def test_missing_cost_or_unsupported_condition_blocks_positive_flag(self):
        for amount in (None, '-1', 'NaN', 'Infinity'):
            self.review['costs']['fees']['eur'] = amount
            self.assertEqual(evaluate(self.record, self.review)['outcome'], 'unresolved')
        self.review['costs']['fees']['eur'] = '2'
        self.review['condition']['status'] = 'unresolved'
        self.assertEqual(evaluate(self.record, self.review)['outcome'], 'unresolved')

    def test_capture_and_price_cannot_be_silently_substituted(self):
        self.review['capture_id'] = 'b'
        with self.assertRaises(ValueError):
            evaluate(self.record, self.review)
        self.review['capture_id'] = 'a'
        self.review['acquisition_price']['eur'] = '5'
        self.assertEqual(evaluate(self.record, self.review)['outcome'], 'unresolved')

    def test_contradiction_and_partial_capture_prevent_supported(self):
        self.review['contradictions'] = ['title 99/200 versus photo 99/220']
        self.assertEqual(evaluate(self.record, self.review)['outcome'], 'unresolved')
        self.review['contradictions'] = []
        self.record['collection_status'] = 'partial'
        self.assertEqual(evaluate(self.record, self.review)['outcome'], 'unresolved')

    def test_wanted_ad_has_explicit_rejection_with_evidence(self):
        self.review['scope'] = {'status': 'excluded', 'reason': 'wanted advertisement', 'evidence': ['Suche']}
        self.assertEqual(evaluate(self.record, self.review)['outcome'], 'unsupported')
        del self.review['scope']['evidence']
        self.assertEqual(evaluate(self.record, self.review)['outcome'], 'unresolved')

    def test_currency_in_description_and_links_are_masked(self):
        text = '97 52 38. Ich habe 89 € bezahlt, Verkaufspreis 50€. EUR 45,50. https://example.de/50-euro'
        masked = mask_text(text)
        self.assertIn('97 52 38', masked)
        for leak in ('89', '50€', '45,50', 'example.de'):
            self.assertNotIn(leak, masked)
        self.assertEqual(mask_text(None), None)

    def test_export_keeps_missing_positions_and_separates_source_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            capture = root / 'source'
            (capture / 'photos').mkdir(parents=True)
            photo = capture / 'photos' / '01.jpg'
            photo.write_bytes(b'synthetic hash fixture')
            record = copy.deepcopy(self.record)
            record['fields'].update({name: {'value': '50 EUR'} for name in ('title', 'description', 'attributes')})
            record['canonical_url'] = 'https://example.de/price-50'
            record['photos'] = [{'index': 1, 'status': 'downloaded', 'local_path': 'photos/01.jpg',
                                 'sha256': hashlib.sha256(photo.read_bytes()).hexdigest()},
                                {'index': 2, 'status': 'failed'}]
            (capture / 'record.json').write_text(json.dumps(record), encoding='utf-8')
            draft = export_packet(capture, root / 'draft')
            self.assertFalse(draft['blind_review_ready'])
            self.assertEqual(len(draft['photos']), 2)
            self.assertEqual(draft['photos'][1]['status'], 'failed')
            self.assertNotIn('canonical_url', draft)
            self.assertNotIn('asking_price', json.dumps(draft))
            self.assertEqual((root / 'draft/photos/01.jpg').read_bytes(), photo.read_bytes())
            with self.assertRaises(FileExistsError):
                export_packet(capture, root / 'draft')
            with self.assertRaises(ValueError):
                export_packet(capture, capture / 'draft')
            photo.write_bytes(b'changed evidence')
            with self.assertRaises(ValueError):
                export_packet(capture, root / 'changed-draft')

    def test_research_normalization_preserves_missing_ids_and_aggregate(self):
        row = {'cells': ['title', '', '50,26 €\nFestpreis', '6,22 €\n0%', '3', '', '-', '23. Aug 2026'], 'links': []}
        snapshot = {'url': 'https://www.ebay.de/sh/research?tabName=SOLD&sellerCountry=SellerLocation%3A%3A%3ADE',
                    'captured_at': '2026-09-12', 'rows': [{}, row]}
        comp = normalize_research(snapshot, 'fixture.json')[0]
        self.assertEqual(comp['item_price_eur'], '50.26')
        self.assertEqual(comp['price_basis'], 'aggregate_average')
        self.assertIsNone(comp['sale_id'])
        self.assertEqual(comp['seller_country'], 'DE')
        self.assertEqual(comp['review_status'], 'pending')
        snapshot['url'] = 'https://www.ebay.de/sh/research?tabName=ACTIVE'
        with self.assertRaises(ValueError):
            normalize_research(snapshot, 'fixture.json')

    def test_price_perturbation_does_not_change_masked_draft_for_supported_syntax(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            capture = root / 'source'
            capture.mkdir()
            drafts = []
            for asking in ('25', '50', '100'):
                record = copy.deepcopy(self.record)
                record['fields'].update({
                    'title': {'value': f'Model 97 52 38 for {asking} EUR'},
                    'description': {'value': f'Originally 89 €, now {asking}€ VB. https://example.de/{asking}-eur'},
                    'attributes': {'value': None}, 'asking_price': {'value': f'{asking} €'}})
                record['photos'] = []
                (capture / 'record.json').write_text(json.dumps(record), encoding='utf-8')
                drafts.append(export_packet(capture, root / ('draft-' + asking)))
            self.assertEqual(drafts[0], drafts[1])
            self.assertEqual(drafts[1], drafts[2])


if __name__ == '__main__':
    unittest.main()
