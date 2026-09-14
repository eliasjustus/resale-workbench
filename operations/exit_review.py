"""Conditional forward choices, separated from sunk historical costs."""
from .bindings import subject_claims
from .clock import parse_timestamp
from .contracts import read_record, record_digest
from .errors import IntegrityError
from .serialization import digest


def assess(review,*,as_of,resolve,verify_claim=None):
    review = read_record(review,expected_type="exit_review")
    ids = [option["option_id"] for option in review["alternatives"]]
    if len(ids) != len(set(ids)):
        raise IntegrityError("Exit alternatives need unique identities")
    check = subject_claims(review["basis_claim_ids"],review,as_of=as_of,resolve=resolve,verify_claim=verify_claim)
    qualified = check["state"] == "pass" and set(review).issubset(check["verified_fields"]) and parse_timestamp(review["created_at"]) <= parse_timestamp(as_of) < parse_timestamp(review["valid_until"])
    alternatives = {}
    for option in review["alternatives"]:
        known = qualified and all(option[field] is not None for field in ("future_receipts_cents","future_cash_costs_cents","future_noncash_costs_cents","future_work_minutes","future_storage_days","delay_days"))
        incremental = option["future_receipts_cents"]-option["future_cash_costs_cents"]-option["future_noncash_costs_cents"] if known else None
        historical = None if incremental is None or review["historical_acquisition_cents"] is None or review["historical_other_costs_cents"] is None else incremental-review["historical_acquisition_cents"]-review["historical_other_costs_cents"]
        alternatives[option["option_id"]] = dict(option,state="pass" if known else "unknown",incremental_contribution_cents=incremental,historical_inclusive_scenario_contribution_cents=historical,action_authorized=False)
    comparable = bool(alternatives) and all(option["state"] == "pass" for option in alternatives.values())
    ordering = sorted(alternatives,key=lambda identity:(-alternatives[identity]["incremental_contribution_cents"],identity)) if comparable else None
    result = {"review_sha256":record_digest(review),"basis":check,"alternatives":alternatives,"conditional_incremental_order":ordering,"historical_profit_status":"scenario_only_not_realized_profit","release_resources":False,"purchase_authorized":False}
    return dict(result,material_sha256=digest(result,domain="projection"))
