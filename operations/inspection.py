"""Evidence-only business-desktop protocol; never executes a device operation."""
from .bindings import subject_claims
from .clock import parse_timestamp
from .contracts import read_record, record_digest
from .errors import IntegrityError

DESKTOP_CHECKS = frozenset(("configuration","power_on","memory","storage_health","display","ports_network","thermal_stability","management_activation","accessories"))
PROHIBITED_ACTIONS = frozenset(("repair","sanitization","data_access","destructive_test","battery_repair","mains_power_repair","safety_critical_repair"))


def assess_inspection(case,protocol,*,as_of,resolve,verify_claim=None):
    case = read_record(case,expected_type="inspection_case")
    protocol = read_record(protocol,expected_type="inspection_protocol")
    now = parse_timestamp(as_of)
    if protocol["record_id"] != case["protocol_id"] or protocol["synthetic"] != case["synthetic"]:
        raise IntegrityError("Inspection protocol identity or synthetic scope differs")
    failures,reasons,checks = [],[],{}
    inputs = {case["record_id"]:record_digest(case),protocol["record_id"]:record_digest(protocol)}
    if protocol["family"] != "business-desktop":
        reasons.append("inspection_family_not_implemented")
    for record in (case,protocol):
        if parse_timestamp(record["created_at"]) > now or parse_timestamp(record["valid_until"]) <= now:
            reasons.append("inspection_input_not_current:"+record["record_id"])
    if case["requested_action"] != "inspect":
        failures.append("requested_intervention_not_available")
    if case["scenario"] != "current_condition":
        reasons.append("conditional_scenario_is_not_current_function")
    for field,expected in case["expected_configuration"].items():
        observed = case["observed_configuration"][field]
        if expected is None or observed is None:
            reasons.append("configuration_unresolved:"+field)
        else:
            mismatched = set(expected) != set(observed) if field == "accessories" else expected != observed
            if mismatched:
                failures.append("wrong_configuration:"+field)
    for role,tool in case["tool_inventory"].items():
        if tool is None:
            reasons.append("required_tool_unavailable:"+role)
    for field in ("access_authorized","management_release","activation_release"):
        if case[field] is False:
            failures.append("inspection_prerequisite_denied:"+field)
        elif case[field] is not True:
            reasons.append("inspection_prerequisite_unresolved:"+field)
    basis = subject_claims(case["basis_claim_ids"],case,as_of=as_of,resolve=resolve,verify_claim=verify_claim)
    checks["case_basis"] = basis
    if not {"case_id","scope","protocol_id","inspector_ref","scenario","requested_action","expected_configuration","observed_configuration","tool_inventory","access_authorized","management_release","activation_release","valid_until"}.issubset(basis["verified_fields"]):
        reasons.append("inspection_scope_tools_identity_or_access_unverified")
    capability = subject_claims(protocol["capability_claim_ids"],protocol,as_of=as_of,resolve=resolve,verify_claim=verify_claim)
    checks["capability"] = capability
    if not {"family","checks","authority_ids","prohibited_operations","valid_until"}.issubset(capability["verified_fields"]):
        reasons.append("inspection_capability_unverified")
    actor = resolve(case["inspector_ref"])
    if actor is None:
        reasons.append("inspection_actor_missing")
    else:
        actor = read_record(actor,expected_type="actor_authority")
        inputs[actor["record_id"]] = record_digest(actor)
        if actor["record_id"] != case["inspector_ref"] or actor["synthetic"] != case["synthetic"]:
            raise IntegrityError("Inspection actor differs from its scope")
        if actor["record_id"] not in protocol["authority_ids"] or "inspect:"+case["scope"] not in actor["scopes"] or actor["revoked_at"] is not None:
            reasons.append("inspection_actor_scope_unavailable")
        if parse_timestamp(actor["created_at"]) > now or parse_timestamp(actor["effective_at"]) > now or parse_timestamp(actor["valid_until"]) <= now:
            reasons.append("inspection_actor_not_current")
        grant = subject_claims(actor["grant_claim_ids"],actor,as_of=as_of,resolve=resolve,verify_claim=verify_claim)
        checks["authority"] = grant
        if not {"identity_ref","scopes","effective_at","valid_until"}.issubset(grant["verified_fields"]):
            reasons.append("inspection_actor_grant_unverified")
    required = {item["check_id"] for item in protocol["checks"]}
    if len(required) != len(protocol["checks"]) or not DESKTOP_CHECKS.issubset(required):
        reasons.append("protocol_coverage_incomplete")
    if not PROHIBITED_ACTIONS.issubset(protocol["prohibited_operations"]):
        reasons.append("protocol_initial_scope_exclusions_missing")
    observation_prerequisites_qualified = not failures and not reasons and all(check["state"] == "pass" for check in checks.values())
    observed = {item["check_id"]:item for item in case["check_results"]}
    if len(observed) != len(case["check_results"]):
        raise IntegrityError("Inspection check has multiple conflicting observations")
    for identity in sorted(required | DESKTOP_CHECKS | set(observed)):
        if identity not in required | DESKTOP_CHECKS:
            reasons.append("observation_outside_reviewed_protocol:"+identity)
        observation = observed.get(identity)
        if observation is None or observation["status"] == "unavailable":
            reasons.append("function_check_unavailable:"+identity)
            continue
        if observation["status"] == "fail":
            failures.append("function_check_failed:"+identity)
        if observation["origin"] != "observed":
            reasons.append("function_not_observed:"+identity)
        check = subject_claims(observation["basis_claim_ids"],case,as_of=as_of,resolve=resolve,verify_claim=verify_claim)
        checks["function:"+identity] = check
        if "check_results" not in check["verified_fields"]:
            reasons.append("function_observation_unverified:"+identity)
    for name,check in checks.items():
        inputs.update(check["input_digests"])
        reasons.extend(check["reason_codes"])
        if check["state"] == "fail":
            failures.append("inspection_claim_failed:"+name)
    state = "fail" if failures else "unknown" if reasons else "pass"
    return {"state":state,"working_item_conclusion":state == "pass" and case["scenario"] == "current_condition","observation_prerequisites_qualified":observation_prerequisites_qualified,"scenario":case["scenario"],"reason_codes":list(dict.fromkeys(failures+reasons)),"checks":checks,"input_digests":inputs,"action_performed":False,"purchase_authorized":False}
