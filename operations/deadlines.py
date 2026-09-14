"""Manually reviewed effective deadlines, never a general legal timer."""
from .bindings import subject_claims
from .clock import parse_timestamp
from .contracts import read_record, record_digest
from .serialization import digest
from .verification import read_verification


def assess_deadline(deadline, *, case_id, route_id, as_of, synthetic, resolve, verify_claim=None):
    if deadline is None:
        return {"state":"unknown","reason_codes":["reviewed_deadline_missing"]}
    deadline = read_record(deadline,expected_type="reviewed_deadline")
    reasons, failed = [], False
    now = parse_timestamp(as_of)
    if (deadline["case_id"],deadline["route_id"],deadline["synthetic"]) != (case_id,route_id,synthetic):
        failed = True
        reasons.append("deadline_context_mismatch")
    if parse_timestamp(deadline["created_at"]) > now or parse_timestamp(deadline["reviewed_at"]) > now:
        reasons.append("deadline_not_available_as_of")
    required = deadline["requirement"]
    if required == "unresolved" or required == "required" and deadline["effective_deadline_at"] is None:
        reasons.append("effective_deadline_unresolved")
    elif required == "required" and parse_timestamp(deadline["effective_deadline_at"]) <= now:
        failed = True
        reasons.append("effective_deadline_passed")
    bound = subject_claims(deadline["basis_claim_ids"],deadline,as_of=as_of,resolve=resolve,verify_claim=verify_claim,claim_type="legal_applicability")
    inputs = dict(bound["input_digests"],**{deadline["record_id"]:record_digest(deadline)})
    reasons.extend(bound["reason_codes"])
    failed |= bound["state"] == "fail"
    if not {"case_id","route_id","trigger_claim_id","effective_deadline_at","requirement"}.issubset(bound["verified_fields"]):
        reasons.append("deadline_applicability_unverified")
    trigger = resolve(deadline["trigger_claim_id"])
    if trigger is None:
        reasons.append("deadline_trigger_missing")
    else:
        trigger = read_record(trigger,expected_type="material_claim")
        inputs[trigger["record_id"]] = record_digest(trigger)
        check = read_verification(verify_claim(deadline["trigger_claim_id"],as_of=as_of,synthetic=synthetic)) if verify_claim else {"state":"unknown"}
        bound["verification_digests"][deadline["trigger_claim_id"]] = digest(check,domain="projection")
        if trigger["record_id"] != deadline["trigger_claim_id"] or trigger["synthetic"] != synthetic or parse_timestamp(trigger["created_at"]) > now or check.get("claim_id") != deadline["trigger_claim_id"] or check.get("state") != "pass" or check.get("claim_sha256") != record_digest(trigger):
            reasons.append("deadline_trigger_unverified")
    return {"state":"fail" if failed else "unknown" if reasons else "pass","reason_codes":reasons,"deadline_id":deadline["record_id"],"effective_deadline_at":deadline["effective_deadline_at"],"limitations":bound["limitations"],"input_digests":inputs,"verification_digests":bound["verification_digests"]}
