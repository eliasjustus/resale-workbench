"""Qualified independent labels; imported answers never establish their own truth."""
from .bindings import subject_claims
from .clock import parse_timestamp
from .contracts import read_record, record_digest
from .decisions import _actor
from .errors import OperationsError


def assess_label(label,case,protocol,records,*,verify_claim=None):
    reasons,checks = [],{}
    if label is None:
        return {"state":"unknown","label":None,"reason_codes":["adjudication_missing"],"checks":{}}
    label = read_record(label,expected_type="benchmark_label")
    by_id = {record["record_id"]:record for record in records}
    if (label["record_id"],record_digest(label),label["case_id"],label["scope"],label["label_scope"],label["synthetic"],label["reviewer_ref"]) != (case["label_id"],case["label_sha256"],case["case_id"],protocol["scope"],protocol["label_scope"],protocol["synthetic"],case["reviewer_ref"]):
        reasons.append("label_manifest_mismatch")
    if parse_timestamp(label["created_at"]) > parse_timestamp(protocol["frozen_at"]):
        reasons.append("label_not_frozen_before_evaluation")
    if set(label["evidence_event_ids"]) != {item["record_id"] for item in case["evidence_refs"]}:
        reasons.append("label_evidence_scope_mismatch")
    checks["label"] = subject_claims(label["basis_claim_ids"],label,as_of=protocol["frozen_at"],resolve=by_id.get,verify_claim=verify_claim)
    if checks["label"]["state"] != "pass" or not set(label).issubset(checks["label"]["verified_fields"]):
        reasons.append("label_basis_unverified")
    reviewers = [label["reviewer_ref"]]
    if label["disagreement"] is None:
        reasons.append("review_disagreement_unknown")
    elif label["disagreement"]:
        if label["adjudicator_ref"] is None or label["adjudicator_ref"] == label["reviewer_ref"]:
            reasons.append("independent_adjudicator_missing")
        else:
            reviewers.append(label["adjudicator_ref"])
        checks["adjudication"] = subject_claims(label["adjudication_claim_ids"],label,as_of=protocol["frozen_at"],resolve=by_id.get,verify_claim=verify_claim)
        if checks["adjudication"]["state"] != "pass" or not {"label","disagreement","adjudicator_ref","uncertainty","severity"}.issubset(checks["adjudication"]["verified_fields"]):
            reasons.append("disagreement_unadjudicated")
    identities = set()
    for reference in reviewers:
        actor = by_id.get(reference)
        if actor is None:
            reasons.append("reviewer_authority_missing:"+reference)
            continue
        actor = read_record(actor,expected_type="actor_authority")
        identity = actor["identity_ref"]
        if actor["synthetic"] != protocol["synthetic"] or identity == protocol["method_identity_ref"] or identity in identities:
            reasons.append("reviewer_not_independent:"+reference)
        identities.add(identity)
        try:
            _,checks[reference] = _actor(list(enumerate(records,1)),reference,identity=identity,scope=protocol["scope"],action="adjudicate",as_of=protocol["frozen_at"],verify_claim=verify_claim)
        except OperationsError as exc:
            reasons.append("reviewer_not_qualified:"+reference+":"+exc.code)
    if label["label"] == "unresolved":
        reasons.append("adjudication_unresolved")
    return {"state":"unknown" if reasons else "pass","label":label["label"] if not reasons else None,"severity":label["severity"],"uncertainty":label["uncertainty"],"reason_codes":reasons,"checks":checks,"label_sha256":record_digest(label)}
