"""Full-cost fixed-amount accounting joined to exact reconciled outcome cash."""
from .bindings import subject_claims
from .clock import parse_timestamp
from .contracts import read_record, record_digest
from .errors import IntegrityError
from .policies import assess_cost_policy, assess_operating_policy
from .serialization import digest


def assess(review,case,*,cutoff_case,cohort_check,cohort_id,scope,synthetic,as_of,cash_account,resolve,verify_claim=None):
    review = read_record(review,expected_type="realized_cost_review")
    reasons,checks = [],{}
    if (review["cohort_id"],review["scope"],review["synthetic"]) != (cohort_id,scope,synthetic):
        raise IntegrityError("Realized accounting differs from its cohort/scope")
    if parse_timestamp(review["created_at"]) > parse_timestamp(as_of) or parse_timestamp(review["as_of"]) > parse_timestamp(as_of):
        reasons.append("realized_review_not_available_as_of")
    if case.get("followup_status") != "closed" or not case.get("measurement_complete",False):
        reasons.append("realized_followup_or_measurements_incomplete")
    if review["outcome_material_sha256"] != case["outcome_material_sha256"]:
        reasons.append("realized_outcome_material_changed")
    if cutoff_case is None or cutoff_case.get("followup_status") != "closed" or not cutoff_case.get("measurement_complete",False) or cutoff_case.get("outcome_material_sha256") != review["outcome_material_sha256"]:
        reasons.append("realized_material_not_qualified_at_review_cutoff")
    cutoff = parse_timestamp(review["as_of"])
    material_ids = set(case["event_ids"]) | {cohort_id,case["inclusion_id"]}
    if case.get("followup_id"):
        material_ids.add(case["followup_id"])
    material_ids.update(case.get("consumption_digests",{}))
    dependency_checks = list(case.get("checks",{}).values())+[case.get("followup_check",{}),case.get("inclusion_check",{}),cohort_check]
    for check in dependency_checks:
        material_ids.update(check.get("input_digests",{}))
    for identity in material_ids:
        material = resolve(identity)
        if material is None or parse_timestamp(material["created_at"]) > cutoff or material.get("as_of") and parse_timestamp(material["as_of"]) > cutoff:
            reasons.append("realized_material_after_review_cutoff:"+identity)
    if review["measured_operator_minutes"] is None or review["measured_operator_minutes"] != case["known_operator_minutes"]:
        reasons.append("actual_operator_time_not_reconciled")
    checks["review"] = subject_claims(review["basis_claim_ids"],review,as_of=as_of,resolve=resolve,verify_claim=verify_claim)
    if checks["review"]["state"] != "pass" or not set(review).issubset(checks["review"]["verified_fields"]):
        reasons.append("full_cost_and_allocation_review_unverified")
    policy = resolve(review["policy_id"])
    if policy is None:
        reasons.append("realized_cost_coverage_policy_missing")
        coverage = set()
    else:
        policy = read_record(policy,expected_type="operating_policy")
        if policy["scope"] != scope or policy["synthetic"] != synthetic:
            raise IntegrityError("Realized policy scope differs")
        checks["policy"] = subject_claims(policy["basis_claim_ids"],policy,as_of=as_of,resolve=resolve,verify_claim=verify_claim)
        checks["policy_assessment"] = assess_operating_policy(policy,as_of=review["as_of"],synthetic=synthetic,verify_claim=verify_claim)
        if checks["policy"]["state"] != "pass" or not set(policy).issubset(checks["policy"]["verified_fields"]) or checks["policy_assessment"]["state"] != "pass":
            reasons.append("realized_policy_not_qualified")
        coverage = set(policy["cost_coverage"])
    ids = [allocation["cash_event_id"] for allocation in review["cash_allocations"]]
    if len(ids) != len(set(ids)):
        raise IntegrityError("A cleared cash movement can be allocated only once")
    if set(ids) != set(case.get("cleared_cash_ids",[])):
        reasons.append("realized_cash_allocation_not_exhaustive")
    categories,underlying,receipts,acquisition = set(),set(),set(),[]
    def exposure(identities):
        if not identities:
            reasons.append("underlying_cost_exposure_missing")
        if underlying.intersection(identities):
            raise IntegrityError("Realized cost allocations overlap an underlying exposure")
        underlying.update(identities)
    def cost_policy(reference,category,amount,occurred_at,key):
        if category not in {"fees","tax"} and reference is None:
            return
        selected = resolve(reference) if reference else None
        check = assess_cost_policy(selected,scope=scope,category=category,amount_cents=amount,as_of=occurred_at,synthetic=synthetic,verify_claim=verify_claim)
        checks[key] = check
        if selected is not None:
            checks[key+":basis"] = subject_claims(selected["basis_claim_ids"],selected,as_of=as_of,resolve=resolve,verify_claim=verify_claim)
            if checks[key+":basis"]["state"] != "pass":
                reasons.append("realized_cost_policy_basis_unverified:"+key)
        if check["state"] != "pass":
            reasons.append("realized_cost_policy_unqualified:"+key)
    for allocation in review["cash_allocations"]:
        exposure(allocation["underlying_exposure_ids"])
        movement = cash_account.get("cash_events",{}).get(allocation["cash_event_id"])
        if movement is None or movement["state"] != "cleared":
            reasons.append("realized_cash_movement_not_cleared")
            continue
        movement_record = resolve(movement["record_id"])
        if movement_record is None or parse_timestamp(movement_record["created_at"]) > cutoff or parse_timestamp(movement["occurred_at"]) > cutoff:
            reasons.append("realized_cash_after_review_cutoff:"+allocation["cash_event_id"])
        if allocation["role"] == "receipt":
            receipts.add(allocation["cash_event_id"])
            if allocation["category"] is not None or allocation["cost_policy_id"] is not None:
                raise IntegrityError("Receipt allocation cannot also claim a cost category")
        elif allocation["role"] == "acquisition":
            acquisition.append(movement["delta"])
            if allocation["category"] is not None or allocation["cost_policy_id"] is not None:
                raise IntegrityError("Acquisition is separately reconciled, not a second policy cost")
        else:
            if allocation["category"] is None:
                reasons.append("cash_cost_category_unknown")
            else:
                categories.add(allocation["category"])
                cost_policy(allocation["cost_policy_id"],allocation["category"],abs(movement["delta"]),movement["occurred_at"],allocation["cash_event_id"])
    if review["acquisition_cost_cents"] is None or review["acquisition_basis"] == "unresolved":
        reasons.append("actual_acquisition_cost_unknown")
    elif review["acquisition_cost_cents"] != -sum(acquisition):
        reasons.append("actual_acquisition_cost_not_reconciled")
    elif review["acquisition_basis"] == "actual_cash_movements" and not acquisition:
        reasons.append("actual_acquisition_cash_evidence_missing")
    elif review["acquisition_basis"] in {"reviewed_zero","not_acquired"} and (acquisition or review["acquisition_cost_cents"] != 0):
        reasons.append("zero_acquisition_basis_conflicts_with_cash")
    if review["acquisition_basis"] == "not_acquired" and (case["physical_state"] != "not_acquired" or "acquired" in case["event_types"]):
        reasons.append("not_acquired_basis_conflicts_with_outcomes")
    noncash,reserves = [],[]
    line_ids = [line["exposure_id"] for line in review["cost_lines"]]
    if len(line_ids) != len(set(line_ids)):
        raise IntegrityError("Realized cost line identities must be unique")
    for line in review["cost_lines"]:
        if line["treatment"] != "reserve":
            categories.add(line["category"])
        exposure(line["underlying_exposure_ids"])
        if line["amount_cents"] is None:
            reasons.append("realized_cost_amount_unknown:"+line["exposure_id"])
        if parse_timestamp(line["incurred_at"]) > parse_timestamp(review["as_of"]):
            reasons.append("realized_cost_not_incurred:"+line["exposure_id"])
        treatment = line["treatment"]
        if treatment == "withheld":
            if line["net_receipt_cash_event_id"] not in receipts:
                reasons.append("withheld_cost_not_bound_to_net_receipt")
        elif line["net_receipt_cash_event_id"] is not None:
            raise IntegrityError("Only withheld costs can name a net receipt")
        if treatment == "noncash":
            noncash.append(line["amount_cents"])
        elif treatment == "reserve":
            reserves.append(line["amount_cents"])
        elif treatment == "explicit_zero" and line["amount_cents"] != 0:
            reasons.append("explicit_zero_has_nonzero_or_unknown_amount")
        cost_policy(line["cost_policy_id"],line["category"],line["amount_cents"],line["incurred_at"],line["exposure_id"])
    if categories != coverage:
        reasons.append("realized_cost_coverage_incomplete_or_unsupported")
    cash_flow = case.get("cleared_cash_delta_cents")
    if cash_flow is None:
        reasons.append("realized_cleared_cash_flow_unresolved")
    resolved = not reasons and cash_flow is not None and all(value is not None for value in noncash)
    result = {"state":"pass" if resolved else "unknown","reason_codes":list(dict.fromkeys(reasons)),"review_sha256":record_digest(review),"checks":checks,"cleared_cash_flow_cents":cash_flow,"noncash_costs_cents":sum(noncash) if all(value is not None for value in noncash) else None,"reserve_cents":sum(reserves) if all(value is not None for value in reserves) else None,"realized_contribution_cents":cash_flow-sum(noncash) if resolved else None,"limitations":review["limitations"],"claim_profitable_operation":False,"purchase_authorized":False}
    return dict(result,material_sha256=digest(result,domain="projection"))
