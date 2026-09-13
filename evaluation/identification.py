"""Validate an isolated identification result's contract, not its factual accuracy."""

import hashlib
import json


def validate_identification(result, input_bytes):
    packet = json.loads(input_bytes)
    errors = []
    if result.get('schema_version') != 2:
        errors.append('expected_schema_version_2')
    if result.get('input_sha256') != hashlib.sha256(input_bytes).hexdigest():
        errors.append('input_hash_mismatch')
    if result.get('asking_price_seen') is not False:
        errors.append('blinding_not_established')
    if result.get('identification_status') not in ('resolved', 'unresolved'):
        errors.append('invalid_identification_status')
    available = {p['index'] for p in packet['photos'] if p['status'] == 'downloaded'}
    scope = result.get('evidence_scope', {})
    opened = scope.get('photos_opened', [])
    if (not isinstance(opened, list) or any(type(i) is not int for i in opened)
            or set(opened) != available or len(opened) != len(available)):
        errors.append('photo_coverage_not_reported_exactly')
    if scope.get('external_sources_used') is not False:
        errors.append('isolated_scope_not_established')
    evidence = result.get('identity', {}).get('evidence', [])
    if result.get('identification_status') == 'resolved' and not evidence:
        errors.append('resolved_identity_has_no_evidence')
    for item in evidence:
        if item.get('source') == 'photo':
            if item.get('photo_index') not in available:
                errors.append('identity_references_unavailable_photo')
        elif item.get('source') == 'text':
            field = item.get('field', '').removeprefix('source_text.')
            text = packet['source_text'].get(field) or ''
            if not item.get('quote') or item['quote'] not in text:
                errors.append('identity_quote_not_in_input')
        else:
            errors.append('unrecognized_identity_evidence_source')
    conflicts = result.get('contradictions')
    if not isinstance(conflicts, list):
        errors.append('contradictions_must_be_array')
    else:
        for conflict in conflicts:
            if not isinstance(conflict, dict) or not all(conflict.get(key) for key in (
                    'left_claim', 'right_claim', 'left_evidence', 'right_evidence')):
                errors.append('conflict_must_identify_both_claims_and_evidence')
            elif not all(isinstance(conflict[key], list) for key in ('left_evidence', 'right_evidence')):
                errors.append('conflict_evidence_must_be_arrays')
    if not isinstance(result.get('unknowns'), list):
        errors.append('unknowns_must_be_explicit_array')
    for claim in result.get('seller_claims', []):
        if claim.get('verified') is not False:
            errors.append('seller_claim_not_separated_from_verified_fact')
    return {'contract_valid': not errors, 'errors': list(dict.fromkeys(errors)),
            'factual_accuracy_verified': False, 'economic_outcome': None,
            'note': 'Coverage/isolation are reviewer reports. A valid schema does not prove '
                    'image interpretation, seller claims, authenticity or value.'}
