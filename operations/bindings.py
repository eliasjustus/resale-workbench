"""Resolve qualified claim results against the exact subject and stored claim."""
from .contracts import read_record, record_digest
from .serialization import digest
from .verification import read_verification


def subject_claims(identities, subject, *, as_of, resolve, verify_claim=None, claim_type=None):
    subject = read_record(subject)
    fields, reasons, limitations, inputs, verifications = set(), [], [], {}, {}
    failed = False
    if not identities or verify_claim is None:
        reasons.append("qualified_subject_claims_missing")
    else:
        for identity in identities:
            claim = resolve(identity)
            if claim is None:
                reasons.append("subject_claim_missing:"+identity)
                continue
            claim = read_record(claim,expected_type="material_claim")
            inputs[identity] = record_digest(claim)
            if claim["record_id"] != identity or claim["synthetic"] != subject["synthetic"] or claim_type is not None and claim["claim_type"] != claim_type:
                failed = True
                reasons.append("subject_claim_identity_or_type_mismatch:"+identity)
                continue
            check = read_verification(verify_claim(identity,as_of=as_of,synthetic=subject["synthetic"]))
            verifications[identity] = digest(check,domain="projection")
            if check.get("claim_id") != identity or check.get("claim_sha256") != record_digest(claim) or check.get("subject") != claim["subject"]:
                failed = True
                reasons.append("subject_claim_snapshot_mismatch:"+identity)
                continue
            if check.get("state") != "pass":
                failed |= check.get("state") == "fail"
                reasons.append("subject_claim_not_verified:"+identity)
                continue
            binding = check["subject"]
            if binding is None or binding["record_id"] != subject["record_id"] or binding["record_type"] != subject["record_type"] or binding["record_sha256"] != record_digest(subject):
                reasons.append("subject_binding_mismatch:"+identity)
                continue
            fields.update(binding["verified_fields"])
            limitations.append({"claim_id":identity,"limitations":check.get("limitations",[]),"review_limitations":check.get("review_limitations",[])})
    return {"state":"fail" if failed else "unknown" if reasons else "pass","verified_fields":sorted(fields),"reason_codes":reasons,"limitations":limitations,"input_digests":inputs,"verification_digests":verifications}
