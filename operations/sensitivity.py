"""Whole-event omission and reviewed fixed scenarios, not confidence intervals."""
from .comparability import assess_comparisons
from .errors import IntegrityError
from .policies import assess_comparison_adjustment
from .serialization import digest


def compare_sensitivity(assessment,events,edges=(),*,as_of,resolve,verify_claim=None,verify_edge=None,assumption_policy_ids=()):
    baseline = assess_comparisons(assessment,events,edges,as_of=as_of,resolve=resolve,verify_claim=verify_claim,verify_edge=verify_edge)
    groups = baseline["material_events"]
    omissions = []
    for removed in groups:
        remaining = [item for item in groups if item["group_id"] != removed["group_id"]]
        omissions.append({"omitted_group_id":removed["group_id"],"omitted_evidence_event_ids":removed["evidence_event_ids"],"remaining_independent_events":len(remaining),"conditional_floor_cents":min((item["price_cents"] for item in remaining),default=None) if baseline["state"] == "pass" else None})
    scenarios = []
    for identity in assumption_policy_ids:
        policy = resolve(identity)
        if policy is not None and policy["record_id"] != identity:
            raise IntegrityError("Sensitivity policy resolver returned another identity")
        check = assess_comparison_adjustment(policy,assessment,evidence_event_id=None,as_of=as_of,resolve=resolve,verify_claim=verify_claim)
        amount = baseline["conditional_floor_cents"]+check["amount_cents"] if baseline["state"] == "pass" and check["state"] == "pass" else None
        if amount is not None and amount < 0:
            amount = None
            check = dict(check,state="unknown",reason_codes=check["reason_codes"]+["scenario_price_below_zero"])
        scenarios.append({"policy_id":identity,"state":check["state"] if baseline["state"] == "pass" else "unknown","conditional_floor_cents":amount,"check":check})
    values = [item["conditional_floor_cents"] for item in scenarios if item["conditional_floor_cents"] is not None]
    complete = bool(scenarios) and all(item["state"] == "pass" and item["conditional_floor_cents"] is not None for item in scenarios)
    return {"baseline":baseline,"baseline_sha256":digest(baseline,domain="projection"),"leave_one_material_event_out":omissions,"assumption_scenarios":scenarios,"conditional_scenario_range_cents":[min(values),max(values)] if complete else None,"range_kind":"reviewed_conditional_scenarios_not_confidence_interval","purchase_authorized":False}
