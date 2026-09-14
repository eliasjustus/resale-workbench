"""Dated source permissions. Record labels alone cannot allow an action."""
from .clock import parse_timestamp
from .contracts import read_record, record_digest
from .errors import ContractError
from .verification import read_verification

SCOPE_FIELDS = ("source_name", "access_route", "account_role", "purpose", "scope")
ACTIONS = frozenset(("collection", "retention", "external_transfer"))


def assess_source(rule, *, action, context, as_of, synthetic, resolve, verify_claim=None):
    """Assess one explicitly selected rule immediately before an action.

    verify_claim(claim_id, as_of=..., synthetic=...) is the material-evidence
    service. Without it, no action is allowed, including manual retention.
    Its result must carry state pass/fail/unknown and explicit reasons.
    """
    if action not in ACTIONS or type(context) is not dict or set(context) != set(SCOPE_FIELDS) or any(type(value) is not str or not value for value in context.values()) or type(synthetic) is not bool:
        raise ContractError("An exact source scope and supported action are required")
    instant = parse_timestamp(as_of)
    result = {"state":"unknown", "status":"permission_unknown", "action":action, "rule_id":None, "reason_codes":[], "evidence_refs":[], "purchase_authorized":False}
    if rule is None:
        result["reason_codes"] = ["source_rule_missing"]
        return result
    rule = read_record(rule, expected_type="source_rule")
    result["rule_id"] = rule["record_id"]
    if rule["synthetic"] != synthetic:
        result.update(state="fail", status="permission_denied", reason_codes=["synthetic_boundary_mismatch"])
        return result
    mismatched = [field for field in SCOPE_FIELDS if rule[field] != context[field]]
    if mismatched:
        result.update(state="fail", status="scope_mismatch", reason_codes=["scope_mismatch:"+field for field in mismatched])
        return result
    if parse_timestamp(rule["created_at"]) > instant or parse_timestamp(rule["checked_at"]) > instant:
        result["reason_codes"].append("source_rule_not_available_as_of")
    if parse_timestamp(rule["valid_until"]) <= instant:
        result["reason_codes"].append("source_rule_expired")
    if rule["unresolved_fields"]:
        result["reason_codes"].append("source_rule_has_unresolved_fields")
    if rule[action] == "denied":
        result.update(state="fail", status="permission_denied")
        result["reason_codes"].append("explicit_source_denial")
    elif rule[action] != "allowed":
        result["reason_codes"].append("source_permission_unresolved")
    if not rule["basis_refs"]:
        result["reason_codes"].append("written_authority_missing")
    if verify_claim is None:
        result["reason_codes"].append("material_authority_verifier_unavailable")
    else:
        for identity in rule["basis_refs"]:
            basis = resolve(identity)
            if basis is None:
                result["reason_codes"].append("authority_claim_missing")
                continue
            basis = read_record(basis, expected_type="material_claim")
            if basis["record_id"] != identity or basis["claim_type"] != "legal_applicability" or basis["synthetic"] != synthetic:
                result.update(state="fail", status="permission_denied")
                result["reason_codes"].append("authority_claim_scope_or_identity_mismatch")
                continue
            assessment = read_verification(verify_claim(identity, as_of=as_of, synthetic=synthetic))
            result["evidence_refs"].append(identity)
            if assessment.get("claim_id") != identity or assessment.get("claim_sha256") != record_digest(basis) or assessment.get("subject") != basis["subject"]:
                result.update(state="fail",status="permission_denied")
                result["reason_codes"].append("authority_verification_snapshot_mismatch")
                continue
            subject = assessment["subject"]
            required_fields = set(SCOPE_FIELDS) | {action}
            if subject is None or subject["record_id"] != rule["record_id"] or subject["record_type"] != "source_rule" or subject["record_sha256"] != record_digest(rule) or not required_fields.issubset(subject["verified_fields"]):
                result["reason_codes"].append("authority_not_bound_to_source_action")
            if assessment["state"] == "fail":
                result.update(state="fail", status="permission_denied")
                result["reason_codes"].append("authority_claim_contradicted")
            elif assessment["state"] != "pass":
                result["reason_codes"].append("authority_claim_unresolved")
    if not result["reason_codes"]:
        result.update(state="pass", status="allowed")
    return result
