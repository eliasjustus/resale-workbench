"""Reviewed fixed-amount cost policy, without a general fee or tax engine."""
from .clock import parse_timestamp
from .contracts import read_record, record_digest
from .config import policy_prerequisites
from .verification import read_verification


def assess_comparison_adjustment(policy,assessment,*,evidence_event_id,as_of,resolve,verify_claim=None):
    """Explicit reviewed cents, scoped to this offer; no percentage heuristic."""
    from .bindings import subject_claims
    from .serialization import digest
    if policy is None:
        return {"state":"unknown","reason_codes":["comparison_adjustment_policy_missing"],"amount_cents":None}
    policy = read_record(policy,expected_type="comparison_adjustment")
    assessment = read_record(assessment,expected_type="comparison_assessment")
    now = parse_timestamp(as_of)
    reasons = []
    offer_digest = digest({"scenario":assessment["scenario"],"offer":assessment["target_offer"]})
    if any(policy[key] != assessment[key] for key in ("case_id","scope","scenario","synthetic")) or policy["offer_sha256"] != offer_digest or policy["evidence_event_id"] != evidence_event_id:
        reasons.append("comparison_adjustment_scope_mismatch")
    if parse_timestamp(policy["created_at"]) > now or parse_timestamp(policy["effective_at"]) > now or parse_timestamp(policy["valid_until"]) <= now:
        reasons.append("comparison_adjustment_not_current")
    if policy["rule"] != "fixed_amount" or policy["amount_cents"] is None:
        reasons.append("comparison_adjustment_rule_unresolved")
    check = subject_claims(policy["basis_claim_ids"],policy,as_of=as_of,resolve=resolve,verify_claim=verify_claim)
    if check["state"] != "pass" or not {"case_id","scope","offer_sha256","scenario","evidence_event_id","rule","amount_cents","effective_at","valid_until"}.issubset(check["verified_fields"]):
        reasons.append("comparison_adjustment_unverified")
    return {"state":"unknown" if reasons else "pass","reason_codes":reasons,"amount_cents":policy["amount_cents"] if not reasons else None,"policy_sha256":record_digest(policy),"checks":check}


def assess_operating_policy(policy, *, as_of, synthetic, verify_claim=None):
    policy = read_record(policy,expected_type="operating_policy")
    reasons = policy_prerequisites(policy,as_of=as_of,synthetic=synthetic)
    fields = {"scope","effective_at","valid_until","authority_refs","source_authority_refs","cash_floor_cents","incremental_stress_cents","labor_cents_per_hour","minimum_contribution_cents","cost_coverage","rounding","contribution_rule","labor_allocation_rule","research_allocation_rule","deadline_policy","clock_skew_seconds","validity_seconds"}
    covered = set()
    if verify_claim is not None:
        for identity in policy["basis_claim_ids"]:
            result = read_verification(verify_claim(identity,as_of=as_of,synthetic=synthetic))
            subject = result.get("subject")
            if result.get("state") == "pass" and result.get("claim_id") == identity and isinstance(subject,dict) and subject.get("record_id") == policy["record_id"] and subject.get("record_type") == "operating_policy" and subject.get("record_sha256") == record_digest(policy):
                covered.update(subject.get("verified_fields",()))
    if not fields.issubset(covered):
        reasons.append("operating_policy_not_substantively_verified")
    return {"state":"unknown" if reasons else "pass","reason_codes":reasons,"policy_sha256":record_digest(policy)}


def assess_cost_policy(policy, *, scope, category, amount_cents, as_of, synthetic, verify_claim=None):
    if policy is None:
        return {"state":"unknown","reason_codes":["cost_policy_missing"]}
    policy = read_record(policy,expected_type="cost_policy")
    reasons = []
    instant = parse_timestamp(as_of)
    if policy["synthetic"] != synthetic:
        reasons.append("cost_policy_synthetic_mismatch")
    if policy["account_scope"] != scope or policy["category"] != category:
        reasons.append("cost_policy_scope_mismatch")
    if parse_timestamp(policy["created_at"]) > instant or parse_timestamp(policy["effective_at"]) > instant or parse_timestamp(policy["valid_until"]) <= instant:
        reasons.append("cost_policy_not_current")
    if policy["rule"] != "fixed_amount":
        reasons.append("unsupported_cost_policy_formula")
    if policy["amount_cents"] is None or policy["amount_cents"] != amount_cents:
        reasons.append("cost_policy_amount_mismatch")
    if not policy["basis_claim_ids"] or verify_claim is None:
        reasons.append("cost_policy_applicability_unverified")
    else:
        for identity in policy["basis_claim_ids"]:
            result = read_verification(verify_claim(identity,as_of=as_of,synthetic=synthetic))
            subject = result.get("subject")
            if result.get("state") != "pass" or result.get("claim_id") != identity or not isinstance(subject,dict) or subject.get("record_id") != policy["record_id"] or subject.get("record_sha256") != record_digest(policy) or subject.get("record_type") != "cost_policy" or not {"account_scope","category","rule","amount_cents","effective_at","valid_until"}.issubset(subject.get("verified_fields",())):
                reasons.append("cost_policy_applicability_unverified")
    return {"state":"unknown" if reasons else "pass","reason_codes":list(dict.fromkeys(reasons)),"policy_id":policy["record_id"],"policy_sha256":record_digest(policy)}
