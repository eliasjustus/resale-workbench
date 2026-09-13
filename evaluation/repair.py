"""Repair evidence contract v1. Structural checks cannot establish factual truth."""


def _strings(value, *, nonempty=False):
    return (isinstance(value, list) and (bool(value) or not nonempty)
            and all(isinstance(item, str) and item.strip() for item in value))


def validate_repair(repair):
    """Return errors, not a diagnosis or valuation. Missing data stays explicit."""
    if not isinstance(repair, dict):
        return ['repair_must_be_object']
    errors = []
    fields = {'schema_version', 'status', 'symptoms', 'hypotheses', 'diagnostics',
              'diagnosis_evidence', 'verification_evidence', 'downside', 'unknowns'}
    if set(repair) != fields:
        errors.append('repair_fields_missing_or_unknown')
    if type(repair.get('schema_version')) is not int or repair['schema_version'] != 1:
        errors.append('repair_schema_version_must_be_1')
    status = repair.get('status')
    if status not in ('not_needed', 'unresolved', 'diagnosed', 'repaired_verified'):
        errors.append('repair_status_invalid')
    for field in ('diagnosis_evidence', 'verification_evidence', 'unknowns'):
        if not _strings(repair.get(field)):
            errors.append('repair_' + field + '_must_be_string_array')
    for field in ('symptoms', 'hypotheses', 'diagnostics'):
        rows = repair.get(field)
        if not isinstance(rows, list):
            errors.append('repair_' + field + '_must_be_array')
            continue
        for index, row in enumerate(rows):
            valid = isinstance(row, dict)
            if valid and field == 'symptoms':
                valid = (set(row) == {'claim', 'source', 'evidence_refs'}
                         and isinstance(row.get('claim'), str) and bool(row['claim'].strip())
                         and row.get('source') in ('observed', 'seller')
                         and _strings(row.get('evidence_refs'), nonempty=True))
            elif valid and field == 'hypotheses':
                valid = (set(row) == {'cause', 'evidence_refs'}
                         and isinstance(row.get('cause'), str) and bool(row['cause'].strip())
                         and _strings(row.get('evidence_refs')))
            elif valid:
                valid = (set(row) == {'test', 'distinguishes', 'equipment', 'availability'}
                         and isinstance(row.get('test'), str) and bool(row['test'].strip())
                         and _strings(row.get('distinguishes'), nonempty=True)
                         and _strings(row.get('equipment'))
                         and row.get('availability') in ('available', 'unavailable', 'unknown'))
            if not valid:
                errors.append(f'repair_{field}_{index}_invalid')
    downside = repair.get('downside')
    if (not isinstance(downside, dict) or set(downside) != {'status', 'evidence_refs'}
            or downside.get('status') not in ('unresolved', 'evidenced')
            or not _strings(downside.get('evidence_refs'))):
        errors.append('repair_downside_invalid')
    elif downside['status'] == 'evidenced' and not downside['evidence_refs']:
        errors.append('repair_downside_evidence_missing')
    if status in ('diagnosed', 'repaired_verified') and not _strings(
            repair.get('diagnosis_evidence'), nonempty=True):
        errors.append('repair_diagnosis_evidence_missing')
    if status in ('not_needed', 'repaired_verified') and not _strings(
            repair.get('verification_evidence'), nonempty=True):
        errors.append('repair_verification_evidence_missing')
    if status == 'repaired_verified' and (not isinstance(downside, dict)
                                        or downside.get('status') != 'evidenced'):
        errors.append('repair_downside_unresolved')
    return errors


def repair_gate_reasons(review):
    """Pending repairs are inspection candidates only in this initial pilot.

    Legacy no-work records remain compatible. Work-required records now need an
    explicit contract; allowances alone cannot resolve uncertain repair outcomes.
    """
    if 'repair' not in review:
        if review.get('work', {}).get('required') is True or review.get('repair_scenarios'):
            return ['repair_contract_missing']
        return []
    repair = review['repair']
    errors = validate_repair(repair)
    if errors:
        return errors
    if repair['status'] in ('unresolved', 'diagnosed'):
        return ['repair_pending_inspection_or_verification']
    return []
