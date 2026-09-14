"""Case-specific route review; provider names never establish protection."""
from .bindings import subject_claims
from .clock import parse_timestamp
from .contracts import read_record, record_digest
from .deadlines import assess_deadline
from .errors import IntegrityError

ROUTE_FIELDS = frozenset(("case_id","purpose","buyer_role","seller_role","provider","jurisdiction","delivery_method","terms_ref","terms_version","item_id","buyer_identity_ref","seller_identity_ref","payment_destination_sha256","acquisition_cents","intervention_plan_sha256","protection_required","protection_status","item_party_payment_binding","inspection_before_release_plan_ref","claim_deadline_review_ref","deadline_ids","intervention_exclusions","valid_until"))


def assess_route(route, *, case_id, as_of, synthetic, resolve, verify_claim=None):
    route = read_record(route,expected_type="transaction_route")
    reasons, failures, checks = [], [], {}
    inputs = {route["record_id"]:record_digest(route)}
    now = parse_timestamp(as_of)
    if route["case_id"] != case_id or route["synthetic"] != synthetic:
        failures.append("route_context_mismatch")
    if parse_timestamp(route["created_at"]) > now:
        reasons.append("route_not_available_as_of")
    if parse_timestamp(route["valid_until"]) <= now:
        reasons.append("route_expired")
    for field in ("item_id","buyer_identity_ref","seller_identity_ref","payment_destination_sha256","acquisition_cents","intervention_plan_sha256","protection_required"):
        if route[field] is None:
            reasons.append("route_field_unresolved:"+field)
    if route["eligibility"] == "ineligible":
        failures.append("reviewed_route_ineligible")
    elif route["eligibility"] != "eligible":
        reasons.append("route_eligibility_unresolved")
    if route["item_party_payment_binding"] == "contradicted":
        failures.append("item_party_payment_binding_contradicted")
    elif route["item_party_payment_binding"] != "established":
        reasons.append("item_party_payment_binding_unresolved")
    if route["protection_required"] is True:
        if route["protection_status"] == "unavailable":
            failures.append("required_protection_unavailable")
        elif route["protection_status"] != "available":
            reasons.append("required_protection_unresolved")
    for name,identities,claim_type,required_fields in (
        ("bindings",route["binding_claim_ids"],None,ROUTE_FIELDS),
        ("ownership",route["ownership_claim_ids"],"ownership",{"item_id","seller_identity_ref"}),
        ("eligibility",route["eligibility_claim_ids"],"legal_applicability",{"purpose","eligibility","protection_required","protection_status"}),
        ("terms",[route["terms_ref"]],"legal_applicability",{"terms_ref","terms_version","intervention_exclusions"}),
        ("deadline_policy",[route["claim_deadline_review_ref"]],"legal_applicability",{"deadline_ids","claim_deadline_review_ref"}),
    ):
        check = subject_claims(identities,route,as_of=as_of,resolve=resolve,verify_claim=verify_claim,claim_type=claim_type)
        checks[name] = check
        inputs.update(check["input_digests"])
        reasons.extend(check["reason_codes"])
        if check["state"] == "fail":
            failures.append("route_claim_failed:"+name)
        if not required_fields.issubset(check["verified_fields"]):
            reasons.append("route_claim_fields_unverified:"+name)
    inspection = resolve(route["inspection_before_release_plan_ref"])
    if inspection is None:
        reasons.append("inspection_protocol_missing")
    else:
        inspection = read_record(inspection,expected_type="inspection_protocol")
        if inspection["record_id"] != route["inspection_before_release_plan_ref"] or inspection["synthetic"] != synthetic:
            raise IntegrityError("Inspection protocol identity or synthetic boundary mismatch")
        inputs[inspection["record_id"]] = record_digest(inspection)
        if parse_timestamp(inspection["created_at"]) > now or parse_timestamp(inspection["valid_until"]) <= now:
            reasons.append("inspection_protocol_not_current")
        if not inspection["checks"] or not inspection["authority_ids"]:
            reasons.append("inspection_protocol_incomplete")
        check = subject_claims(inspection["capability_claim_ids"],inspection,as_of=as_of,resolve=resolve,verify_claim=verify_claim)
        checks["inspection"] = check
        inputs.update(check["input_digests"])
        if check["state"] != "pass" or not {"family","checks","authority_ids","prohibited_operations","valid_until"}.issubset(check["verified_fields"]):
            reasons.append("inspection_capability_or_authority_unverified")
    if not route["deadline_ids"]:
        reasons.append("reviewed_deadlines_missing")
    for identity in route["deadline_ids"]:
        deadline = resolve(identity)
        if deadline is not None and deadline["record_id"] != identity:
            raise IntegrityError("Deadline resolver returned a different identity")
        check = assess_deadline(deadline,case_id=case_id,route_id=route["record_id"],as_of=as_of,synthetic=synthetic,resolve=resolve,verify_claim=verify_claim)
        checks["deadline:"+identity] = check
        inputs.update(check.get("input_digests",{}))
        reasons.extend(check["reason_codes"])
        if check["state"] == "fail":
            failures.append("route_deadline_failed:"+identity)
        if deadline is not None:
            inputs[identity] = record_digest(deadline)
    state = "fail" if failures else "unknown" if reasons else "pass"
    return {"state":state,"eligibility":"ineligible" if failures else "unresolved" if reasons else "eligible","reason_codes":list(dict.fromkeys(failures+reasons)),"route_id":route["record_id"],"route_sha256":record_digest(route),"input_digests":inputs,"checks":checks,"residual_risks":list(route["residual_risks"]),"purchase_authorized":False}
