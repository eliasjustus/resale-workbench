"""Fixed-cost operating scenarios, separate from the unchanged economic screen."""
from .clock import parse_timestamp
from .contracts import read_record, record_digest
from .errors import ContractError, IntegrityError
from .policies import assess_cost_policy, assess_operating_policy
from .upstream import recheck
from .verification import read_verification


def assess_scenario(review, policy, upstream, *, resolve, verify_claim=None, receipt_shocks_cents=()):
    review = read_record(review,expected_type="operating_review")
    policy = read_record(policy,expected_type="operating_policy")
    upstream = read_record(upstream,expected_type="upstream_evaluation")
    reasons = assess_operating_policy(policy,as_of=review["as_of"],synthetic=review["synthetic"],verify_claim=verify_claim)["reason_codes"]
    if review["policy_id"] != policy["record_id"] or review["scope"] != policy["scope"]:
        reasons.append("operating_policy_scope_mismatch")
    if any(review[key] != upstream[target] for key,target in (("upstream_evaluation_id","record_id"),("case_id","case_id"),("synthetic","synthetic"),("capture_sha256","capture_sha256"),("review_sha256","review_sha256"),("upstream_result_sha256","result_sha256"))):
        raise IntegrityError("Operating scenario differs from its upstream evaluation binding")
    upstream_result = recheck(upstream)
    if parse_timestamp(upstream["created_at"]) > parse_timestamp(review["as_of"]):
        reasons.append("upstream_not_available_as_of")
    if parse_timestamp(upstream["as_of"]) > parse_timestamp(review["as_of"]) or parse_timestamp(upstream["as_of"]).date() != parse_timestamp(review["as_of"]).date():
        reasons.append("upstream_view_date_mismatch")
    if policy["validity_seconds"] is not None and (parse_timestamp(review["as_of"])-parse_timestamp(upstream["created_at"])).total_seconds() > policy["validity_seconds"]:
        reasons.append("upstream_snapshot_expired")
    if upstream_result["outcome"] != "supported":
        reasons.append("upstream_screen_"+upstream_result["outcome"])
    if review["scenario_kind"] == "forecast" and not review["assumptions"]:
        reasons.append("forecast_assumptions_missing")
    if review["scenario_kind"] == "realized":
        reasons.append("realized_requires_dedicated_cost_review")
    primary_fields = {"item_receipt_cents","shipping_collected_cents","acquisition_cents","receipts_basis","included_cost_exposure_ids","scenario_kind","scope"}
    covered = set()
    if verify_claim is not None:
        for identity in review["basis_claim_ids"]:
            check = read_verification(verify_claim(identity,as_of=review["as_of"],synthetic=review["synthetic"]))
            subject = check.get("subject")
            if check.get("state") == "pass" and check.get("claim_id") == identity and isinstance(subject,dict) and subject.get("record_id") == review["record_id"] and subject.get("record_type") == "operating_review" and subject.get("record_sha256") == record_digest(review):
                covered.update(subject.get("verified_fields",()))
    if not primary_fields.issubset(covered):
        reasons.append("scenario_inputs_not_substantively_verified")
    lines = review["cost_lines"]
    categories = {line["category"] for line in lines}
    for category in policy["cost_coverage"]:
        if category not in categories:
            reasons.append("required_cost_missing:"+category)
    if categories - set(policy["cost_coverage"]):
        reasons.append("cost_categories_not_covered_by_policy")
    exposures, underlying = {}, set()
    for line in lines:
        exposures[line["exposure_id"]] = line
        if line["treatment"] != "reserve":
            if underlying.intersection(line["underlying_exposure_ids"]):
                raise IntegrityError("Cash/noncash allocations overlap an underlying exposure")
            underlying.update(line["underlying_exposure_ids"])
        if line["amount_cents"] is None or line["basis_kind"] == "unresolved":
            reasons.append("cost_amount_or_basis_unknown:"+line["exposure_id"])
        if line["effective_until"] and parse_timestamp(line["effective_until"]) <= parse_timestamp(review["as_of"]):
            reasons.append("cost_basis_expired:"+line["exposure_id"])
        if not line["basis_claim_ids"] or verify_claim is None:
            reasons.append("cost_basis_unverified:"+line["exposure_id"])
        else:
            for identity in line["basis_claim_ids"]:
                check = read_verification(verify_claim(identity,as_of=review["as_of"],synthetic=review["synthetic"]))
                subject = check.get("subject")
                if check.get("state") != "pass" or check.get("claim_id") != identity or not isinstance(subject,dict) or subject.get("record_id") != review["record_id"] or subject.get("record_type") != "operating_review" or subject.get("record_sha256") != record_digest(review) or "cost_lines" not in subject.get("verified_fields",()):
                    reasons.append("cost_basis_unverified:"+line["exposure_id"])
        if line["category"] in {"fees","tax"} or line["cost_policy_id"] is not None:
            selected = resolve(line["cost_policy_id"]) if line["cost_policy_id"] is not None else None
            if selected is not None and selected["record_id"] != line["cost_policy_id"]:
                raise IntegrityError("Cost policy resolver returned a different identity")
            check = assess_cost_policy(selected,scope=review["scope"],category=line["category"],amount_cents=line["amount_cents"],as_of=review["as_of"],synthetic=review["synthetic"],verify_claim=verify_claim)
            reasons.extend(code+":"+line["exposure_id"] for code in check["reason_codes"])
    included = set(review["included_cost_exposure_ids"])
    if review["receipts_basis"] == "gross" and included:
        raise IntegrityError("Gross receipts cannot already include withheld cost exposures")
    if any(identity not in exposures or exposures[identity]["treatment"] != "cash" for identity in included):
        raise IntegrityError("Net receipts must name existing cash exposures already deducted")
    cash = [line["amount_cents"] for line in lines if line["treatment"] == "cash" and line["exposure_id"] not in included]
    noncash = [line["amount_cents"] for line in lines if line["treatment"] == "noncash"]
    reserves = [line["amount_cents"] for line in lines if line["treatment"] == "reserve"]
    incoming = [review["item_receipt_cents"],review["shipping_collected_cents"]]
    cash_known = all(value is not None for value in cash+incoming+[review["acquisition_cents"]])
    cash_margin = sum(incoming)-review["acquisition_cents"]-sum(cash) if cash_known else None
    contribution = cash_margin-sum(noncash) if cash_known and all(value is not None for value in noncash) else None
    if any(value is None for value in incoming+[review["acquisition_cents"]]):
        reasons.append("receipts_or_acquisition_unknown")
    if review["scenario_status"] != "complete":
        reasons.append("scenario_not_complete")
    if policy["contribution_rule"] != "receipts_minus_all_costs":
        reasons.append("unsupported_contribution_rule")
    resolved = not reasons and contribution is not None
    floor = policy["minimum_contribution_cents"]
    state = "pass" if resolved and contribution >= floor else "fail" if resolved else "unknown"
    if state == "fail":
        reasons.append("below_required_contribution")
    maximum = sum(incoming)-sum(cash)-sum(noncash)-floor if resolved else None
    shocks = []
    for shock in receipt_shocks_cents:
        if type(shock) is not int:
            raise ContractError("Sensitivity shocks must be signed integer cents")
        shocks.append({"receipt_delta_cents":shock,"contribution_cents":contribution+shock if resolved else None})
    return {"state":state,"reason_codes":list(dict.fromkeys(reasons)),"operating_review_id":review["record_id"],"operating_review_sha256":record_digest(review),"policy_sha256":record_digest(policy),"upstream_evaluation_sha256":record_digest(upstream),"cash_margin_preview_cents":cash_margin,"contribution_preview_cents":contribution,"contribution_cents":contribution if resolved else None,"reserve_cents":sum(reserves) if all(value is not None for value in reserves) else None,"maximum_acquisition_cents":maximum,"maximum_acquisition_status":"fixed_policy_calculation" if resolved else "unsupported_or_unresolved","sensitivity":shocks,"purchase_authorized":False}
