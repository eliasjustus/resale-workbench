"""No model calls: reviewed evidence in, an auditable screening outcome out."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re

from .repair import repair_gate_reasons
from .evidence import strings
from pilot.config import economic_policy


def money(value):
    if value is None or isinstance(value, bool):
        raise ValueError('Missing monetary amount')
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('Invalid monetary amount') from exc
    if not result.is_finite() or result < 0:
        raise ValueError('Amounts must be finite and nonnegative')
    return result


def evaluate(record, review, policy=None):
    """Fail closed. Human/model judgments must have evidence and remain inspectable.

    'supported' is a conditional evidence-supported margin, never permission to buy.
    Prices are item prices excluding shipping; shipping is a separate cost.
    The minimum observed comparable is a screening scenario, not a price guarantee.
    """
    configured = policy is not None
    policy = economic_policy(policy)
    if (review['listing_id'], review['capture_id']) != (record['listing_id'], record['capture_id']):
        raise ValueError('Review refers to a different immutable capture')
    version = review.get('schema_version', 1)
    if type(version) is not int or version not in (1, 2):
        raise ValueError('Unknown economic review schema version')
    cutoff = date.fromisoformat(review['as_of'])
    reasons, accepted, excluded = [], [], []
    output = {'listing_id': record['listing_id'], 'capture_id': record['capture_id'],
              'opportunity_policy': 'net-profit-20-plus-work-v1' if version == 1 else 'transaction-adequacy-20-plus-work-v2',
              'as_of': cutoff.isoformat(), 'outcome': 'unresolved', 'reasons': reasons,
              'accepted_comps': accepted, 'excluded_comps': excluded, 'arithmetic': None,
              'purchase_authorized': False, 'review_mode': review.get('review_mode', 'unspecified')}
    if configured:
        output['opportunity_policy'] = 'configured-germany-eur-v1'
        output['economic_policy'] = policy
    scope = review.get('scope', {})
    if scope.get('status') == 'excluded' and scope.get('reason') and scope.get('evidence'):
        output['outcome'] = 'unsupported'
        reasons.append('scope_excluded: ' + scope['reason'])
        return output
    if scope.get('status') != 'included' or not scope.get('evidence'):
        reasons.append('scope_not_established')
    if record.get('collection_status') != 'complete':
        reasons.append('incomplete_capture')
    for name in ('identity', 'condition', 'gallery'):
        item = review.get(name, {})
        if item.get('status') != 'resolved' or not item.get('evidence'):
            reasons.append(name + '_unresolved')
    if review.get('contradictions'):
        reasons.append('unresolved_contradictions')
    reasons.extend(repair_gate_reasons(review))

    seen = set()
    for index, comp in enumerate(review.get('comps', [])):
        why = []
        identity = comp.get('evidence_id') if version == 2 else comp.get('sale_id')
        if not identity or (version == 2 and (not isinstance(identity, str) or not identity.strip())):
            identity = None
            why.append('no_unique_evidence_id' if version == 2 else 'no_unique_sale_id')
        elif identity in seen:
            why.append('duplicate_sale')
        else:
            seen.add(identity)
        if version == 2:
            captured_at = comp.get('captured_at')
            valid_capture_time = False
            if isinstance(captured_at, str):
                try:
                    valid_capture_time = datetime.fromisoformat(captured_at.replace('Z', '+00:00')).tzinfo is not None
                except ValueError:
                    pass
            if (comp.get('kind') != 'transaction' or not strings(comp.get('source_paths'), nonempty=True)
                    or not isinstance(comp.get('locator'), str) or not comp['locator'].strip()
                    or not valid_capture_time):
                why.append('transaction_provenance_missing')
        if comp.get('review_status') != 'accepted' or not comp.get('review_evidence'):
            why.append('not_audited_match')
        # Working/repaired/parts-out scenarios cannot silently stand in for the
        # captured target's present condition. Legacy no-work records are unchanged.
        basis = comp.get('condition_basis')
        if (basis is not None and basis != 'current_condition') or (
                ('repair' in review or version == 2) and basis != 'current_condition'):
            why.append('not_current_condition_comparable')
        if comp.get('units_sold') != 1:
            why.append('aggregate_or_unknown_sale_count')
        if comp.get('price_basis') != 'actual_sold_item_price' or comp.get('currency') != 'EUR':
            why.append('unsupported_price_basis')
        if comp.get('seller_country') != 'DE':
            why.append('outside_germany_or_unknown')
        try:
            age = (cutoff - date.fromisoformat(comp.get('sold_at', ''))).days
            if age < 0 or age > policy['sold_max_age_days']:
                why.append(f'outside_preceding_{policy["sold_max_age_days"]}_days')
        except (ValueError, TypeError):
            why.append('unknown_sale_date')
        if identity and identity == review.get('target_evidence_id' if version == 2 else 'target_sale_id'):
            why.append('target_leakage')
        try:
            amount = money(comp.get('item_price_eur'))
            if amount == 0:
                why.append('zero_price')
        except ValueError:
            why.append('missing_or_invalid_price')
        if why:
            excluded.append({'index': index, ('evidence_id' if version == 2 else 'sale_id'): identity, 'reasons': why,
                             'review_reason': comp.get('review_reason')})
        else:
            accepted.append({('evidence_id' if version == 2 else 'sale_id'): identity, 'item_price_eur': str(amount)})
    if version == 1:
        if len(accepted) < 5:
            reasons.append('fewer_than_five_audited_comps')
    else:
        assessment = review.get('transaction_adequacy', {})
        ids = assessment.get('evidence_ids') if isinstance(assessment, dict) else None
        if (not accepted or not isinstance(assessment, dict) or assessment.get('status') != 'sufficient'
                or not isinstance(assessment.get('reason'), str) or not assessment['reason'].strip()
                or not isinstance(assessment.get('limitations'), list)
                or not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)
                or len(ids) != len(set(ids)) or set(ids) != {c['evidence_id'] for c in accepted}):
            reasons.append('transaction_evidence_adequacy_not_established')

    costs = review.get('costs', {})
    cost_values = {}
    work = review.get('work', {})
    work_premium = Decimal(0)
    if work.get('required') is False and work.get('evidence'):
        pass
    elif work.get('required') is True and work.get('evidence'):
        try:
            cost_values['repair_materials'] = money(work.get('repair_materials_eur'))
            work_premium = money(work.get('additional_profit_eur'))
            if work_premium <= 0 or not work.get('basis'):
                raise ValueError('Work needs an explicit positive allowance and basis')
        except ValueError:
            reasons.append('work_cost_or_profit_allowance_unknown')
    else:
        reasons.append('work_requirement_unresolved')
    for name in ('acquisition_transport', 'resale_shipping', 'fees', 'packaging', 'defect_allowance'):
        item = costs.get(name, {})
        try:
            cost_values[name] = money(item.get('eur'))
            if not item.get('basis'):
                raise ValueError('No cost basis')
        except ValueError:
            reasons.append('cost_unknown: ' + name)
    ask = review.get('acquisition_price', {})
    try:
        asking = money(ask.get('eur'))
        if ask.get('source_text') != record['fields']['asking_price']['value']:
            raise ValueError('Price source does not match capture')
        price_match = re.fullmatch(r'\s*(\d+(?:\.\d{3})*(?:,\d{1,2})?)\s*€(?:\s*VB)?\s*',
                                   ask.get('source_text') or '')
        if not price_match or money(price_match[1].replace('.', '').replace(',', '.')) != asking:
            raise ValueError('Amount does not match captured EUR asking price')
    except ValueError:
        reasons.append('acquisition_price_unverified')
    if reasons:
        return output
    conservative = min(money(c['item_price_eur']) for c in accepted)
    total_costs = sum(cost_values.values(), Decimal(0))
    net = conservative - total_costs
    margin = net - asking
    required_profit = Decimal(policy['minimum_profit_eur']) + work_premium
    output['arithmetic'] = {k: str(v.quantize(Decimal('0.01'))) for k, v in {
        'conservative_sale_scenario_eur': conservative, 'costs_eur': total_costs,
        'net_proceeds_eur': net, 'acquisition_price_eur': asking, 'margin_eur': margin,
        'required_profit_eur': required_profit,
        'maximum_acquisition_price_eur': net - required_profit}.items()}
    output['outcome'] = 'supported' if margin >= required_profit else 'unsupported'
    reasons.append('meets_required_net_profit' if margin >= required_profit else 'below_required_net_profit')
    return output
