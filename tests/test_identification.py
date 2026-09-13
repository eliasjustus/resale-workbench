import copy
import hashlib
import json
import unittest

from evaluation.identification import validate_identification


class IdentificationTests(unittest.TestCase):
    def setUp(self):
        self.input = json.dumps({'photos': [{'index': 1, 'status': 'downloaded'}],
                                 'source_text': {'title': 'Exact model 123'}}).encode()
        self.result = {'schema_version': 2, 'input_sha256': hashlib.sha256(self.input).hexdigest(),
                       'asking_price_seen': False, 'identification_status': 'resolved',
                       'evidence_scope': {'photos_opened': [1], 'external_sources_used': False},
                       'identity': {'evidence': [{'source': 'photo', 'photo_index': 1}]},
                       'contradictions': [], 'unknowns': [], 'seller_claims': []}

    def test_valid_contract_is_not_factual_or_economic_verification(self):
        result = validate_identification(self.result, self.input)
        self.assertTrue(result['contract_valid'])
        self.assertFalse(result['factual_accuracy_verified'])
        self.assertIsNone(result['economic_outcome'])

    def test_reject_legacy_conflicts_containing_nonconflicts(self):
        self.result['contradictions'] = [{'issue': 'No direct identity contradiction is visible.'}]
        self.assertFalse(validate_identification(self.result, self.input)['contract_valid'])

    def test_reject_source_substitution_missing_image_and_price_leak(self):
        for mutation in ({'input_sha256': 'wrong'}, {'asking_price_seen': True},
                         {'asking_price_seen': 'false'},
                         {'evidence_scope': {'photos_opened': [], 'external_sources_used': False}}):
            with self.subTest(mutation=mutation):
                result = copy.deepcopy(self.result)
                result.update(mutation)
                self.assertFalse(validate_identification(result, self.input)['contract_valid'])

    def test_reject_fabricated_quote_or_photo_reference(self):
        for evidence in ({'source': 'photo', 'photo_index': 2},
                         {'source': 'text', 'field': 'source_text.title', 'quote': 'Invented model'}):
            self.result['identity']['evidence'] = [evidence]
            self.assertFalse(validate_identification(self.result, self.input)['contract_valid'])

    def test_seller_claim_cannot_be_promoted_by_schema(self):
        self.result['seller_claims'] = [{'claim': 'Unused', 'verified': True}]
        self.assertFalse(validate_identification(self.result, self.input)['contract_valid'])


if __name__ == '__main__':
    unittest.main()
