"""As-of transaction annotations; never rewrite original labels or captures."""
from .clock import parse_timestamp
from .contracts import read_record
from .errors import IntegrityError


def event_view(records, *, as_of, synthetic):
    instant = parse_timestamp(as_of)
    visible, excluded = [], []
    identities = set()
    for value in records:
        record = read_record(value, expected_type="evidence_event")
        if record["record_id"] in identities:
            raise IntegrityError("Duplicate event record identity")
        identities.add(record["record_id"])
        if record["synthetic"] != synthetic:
            raise IntegrityError("Event view cannot mix real and synthetic records")
        reasons = []
        if any(parse_timestamp(record[field]) > instant for field in ("created_at", "observed_at")) or record["event_at"] is not None and parse_timestamp(record["event_at"]) > instant:
            reasons.append("look_ahead_evidence")
        if reasons:
            excluded.append({"record_id":record["record_id"], "reason_codes":reasons})
        else:
            visible.append(record)
    by_id = {record["record_id"]:record for record in visible}
    affected = {}
    for record in visible:
        if record["transaction_state"] in {"refunded", "refund_adjusted", "disputed"}:
            for identity in record["related_event_ids"]:
                target = by_id.get(identity)
                if target is None:
                    continue
                if target["kind"] != "transaction" or target["event_id"] != record["event_id"] or target["event_id"] is None:
                    raise IntegrityError("Reversal must reference the same established transaction identity")
                for candidate in visible:
                    if candidate["kind"] == "transaction" and candidate["event_id"] == target["event_id"]:
                        affected.setdefault(candidate["record_id"], []).append(record["record_id"])
    return {"as_of":as_of, "records":visible, "excluded":excluded, "reversals":affected, "purchase_authorized":False}


def price_eligibility(record, *, as_of, synthetic, reversal_ids=()):
    view = event_view([record], as_of=as_of, synthetic=synthetic)
    record = read_record(record, expected_type="evidence_event")
    reasons = [reason for item in view["excluded"] for reason in item["reason_codes"]]
    if record["kind"] != "transaction":
        reasons.append("not_transaction_evidence")
    if record["transaction_state"] not in {"sold_reported", "paid", "settled"}:
        reasons.append("transaction_state_not_price_support")
    if record["price_basis"] != "actual_sold_item_price":
        reasons.append("unverified_final_item_price")
    if record["item_price_cents"] is None:
        reasons.append("item_price_unknown")
    if record["units"] != 1:
        reasons.append("unit_count_not_single_item")
    if record["seller_country"] != "DE":
        reasons.append("unsupported_seller_country")
    if record["event_at"] is None:
        reasons.append("transaction_time_unknown")
    if record["lineage_status"] != "established" or record["event_id"] is None:
        reasons.append("transaction_identity_unresolved")
    if record["condition_basis"] != "current_condition":
        reasons.append("condition_not_current")
    if reversal_ids:
        reasons.append("later_reversal_requires_review")
    return {"record_id":record["record_id"], "state":"unknown" if reasons else "pass", "reason_codes":reasons, "transaction_state":record["transaction_state"], "purchase_authorized":False}
