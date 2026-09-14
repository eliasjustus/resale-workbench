"""Nonmutating adapter candidates. Existing retained-evidence sealing still applies."""
from .contracts import record_digest
from .events import event_view, price_eligibility
from .verification import read_verification

PRICE_FIELDS = frozenset(("kind", "transaction_state", "price_basis", "item_price_cents", "currency", "units", "seller_country", "condition_basis", "event_at", "event_id"))


def adapt_events(records, *, as_of, synthetic, verify_claim=None):
    view = event_view(records, as_of=as_of, synthetic=synthetic)
    accepted, rejected = [], list(view["excluded"])
    for record in view["records"]:
        check = price_eligibility(record, as_of=as_of, synthetic=synthetic, reversal_ids=view["reversals"].get(record["record_id"], ()))
        reasons = list(check["reason_codes"])
        if not record["material_claim_ids"]:
            reasons.append("verified_price_claim_missing")
        if verify_claim is None:
            reasons.append("material_claim_verifier_unavailable")
        else:
            verified_fields = set()
            for identity in record["material_claim_ids"]:
                result = read_verification(verify_claim(identity, as_of=as_of, synthetic=synthetic))
                if result.get("state") != "pass" or result.get("claim_id") != identity:
                    reasons.append("material_claim_not_verified:"+identity)
                    continue
                subject = result.get("subject")
                if not isinstance(subject, dict) or subject.get("record_id") != record["record_id"] or subject.get("record_type") != "evidence_event" or subject.get("record_sha256") != record_digest(record):
                    reasons.append("claim_not_bound_to_event:"+identity)
                    continue
                verified_fields.update(subject.get("verified_fields", ()))
            if not PRICE_FIELDS.issubset(verified_fields):
                reasons.append("price_fields_not_substantively_verified")
        if reasons:
            rejected.append({"record_id":record["record_id"], "reason_codes":reasons})
        else:
            # Explicit candidates are not sealed evaluator comparables. That
            # adapter must still bind verified locators and required match fields.
            accepted.append({"record_id":record["record_id"], "event_id":record["event_id"], "price_basis":"actual_sold_item_price", "item_price_cents":record["item_price_cents"], "currency":"EUR", "source_label_verbatim":record["source_label_verbatim"], "transaction_state":record["transaction_state"], "material_claim_ids":list(record["material_claim_ids"])})
    return {"as_of":as_of, "candidates":accepted, "rejected":rejected, "assumptions":[], "purchase_authorized":False}
