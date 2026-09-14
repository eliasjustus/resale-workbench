"""As-of physical/financial observations, qualified cash references and corrections."""
from .bindings import subject_claims
from .clock import parse_timestamp
from .contracts import read_record, record_digest
from .errors import IntegrityError
from .serialization import digest

PHYSICAL_STATES = frozenset(("not_acquired","owned","listed","shipped","delivered","returned","disposed","unknown"))
FINANCIAL_STATES = frozenset(("no_transaction","unpaid","payout_held","settled","refund_pending","refunded","claim_open","claim_closed","unknown"))


def active_outcomes(records,*,as_of,cohort_id,synthetic):
    now = parse_timestamp(as_of)
    visible,old,excluded = {},set(),[]
    for record in records:
        if record["record_type"] != "outcome_event" or record["cohort_id"] != cohort_id:
            continue
        record = read_record(record,expected_type="outcome_event")
        if record["synthetic"] != synthetic:
            raise IntegrityError("Cohort cannot mix synthetic and real outcomes")
        if parse_timestamp(record["created_at"]) > now or parse_timestamp(record["occurred_at"]) > now:
            excluded.append(record["record_id"])
            continue
        if record["record_id"] in visible:
            raise IntegrityError("Duplicate outcome identity")
        prior = record["supersedes_event_id"]
        if prior is not None:
            previous = visible.get(prior)
            if previous is None or prior in old or previous["case_id"] != record["case_id"] or previous["scope"] != record["scope"]:
                raise IntegrityError("Outcome correction must replace one earlier same-case observation")
            old.add(prior)
        visible[record["record_id"]] = record
    active = sorted((record for identity,record in visible.items() if identity not in old),key=lambda record:parse_timestamp(record["occurred_at"]))
    return {"active":active,"superseded_ids":sorted(old),"not_available_as_of":excluded}


def summarize_outcomes(records,*,as_of,cohort_id,synthetic,scope,cash_account,resolve,verify_claim=None):
    active = active_outcomes(records,as_of=as_of,cohort_id=cohort_id,synthetic=synthetic)
    cases,allocations,consumptions,cash_owners = {},{},{},{}
    for record in records:
        if record["record_type"] != "resource_action" or record["operation"] != "consume" or record["scope"] != scope or record["synthetic"] != synthetic or parse_timestamp(record["created_at"]) > parse_timestamp(as_of):
            continue
        reservation = cash_account.get("reservations",{}).get(record["reservation_id"])
        if reservation is not None:
            consumptions.setdefault(reservation["case_id"],[]).append(record)
            if record["cash_event_id"] is not None:
                cash_owners[record["cash_event_id"]] = reservation["case_id"]
    cohort_ids = {record["cohort_id"] for record in records if record["record_type"] == "outcome_event" and record["scope"] == scope and record["synthetic"] == synthetic}
    for identity in cohort_ids:
        for event in active_outcomes(records,as_of=as_of,cohort_id=identity,synthetic=synthetic)["active"]:
            if event["scope"] == scope and event["cash_state"] == "cleared" and event["cash_event_id"] is not None:
                allocations.setdefault(event["cash_event_id"],[]).append(event["record_id"])
    for event in active["active"]:
        if event["scope"] != scope:
            raise IntegrityError("Outcome scope differs from cohort scope")
        case = cases.setdefault(event["case_id"],{"event_ids":[],"event_types":[],"physical_state":"unknown","financial_state":"unknown","known_operator_minutes":0,"unknown_time_events":0,"known_cleared_cash_delta_cents":0,"unknown_cash_events":0,"held_cash_ids":[],"reason_codes":[],"checks":{},"last_occurred_at":None})
        identity = event["record_id"]
        case.setdefault("event_digests",{})[identity] = record_digest(event)
        case.setdefault("event_limitations",{})[identity] = event["limitations"]
        case["event_ids"].append(identity)
        case["event_types"].append(event["event_type"])
        case["physical_state"] = event["physical_state"] if event["physical_state"] in PHYSICAL_STATES else "unknown"
        case["financial_state"] = event["financial_state"] if event["financial_state"] in FINANCIAL_STATES else "unknown"
        case["last_occurred_at"] = event["occurred_at"]
        check = subject_claims(event["basis_refs"],event,as_of=as_of,resolve=resolve,verify_claim=verify_claim)
        case["checks"][identity] = check
        qualified = check["state"] == "pass" and {"case_id","cohort_id","scope","event_type","occurred_at","physical_state","financial_state","cash_event_id","cash_state","cash_delta_cents","operator_minutes","measurement_basis"}.issubset(check["verified_fields"]) and event["measurement_basis"] == "actual"
        if not qualified:
            case["reason_codes"].append("outcome_measurement_unverified:"+identity)
        if event["operator_minutes"] is None or not qualified:
            case["unknown_time_events"] += 1
        else:
            case["known_operator_minutes"] += event["operator_minutes"]
        cash_id = event["cash_event_id"]
        delta = event["cash_delta_cents"]
        state = event["cash_state"]
        if state == "not_applicable" and delta == 0 and cash_id is None and qualified:
            continue
        movement = cash_account.get("cash_events",{}).get(cash_id)
        if state == "held" and cash_id is not None:
            case["held_cash_ids"].append(cash_id)
            historical = next((record for record in reversed(records) if record["record_type"] == "resource_action" and record["scope"] == scope and record["synthetic"] == synthetic and record["cash_event_id"] == cash_id and record["cash_state"] == "held" and record["cash_delta_cents"] == delta and parse_timestamp(record["occurred_at"]) <= parse_timestamp(event["occurred_at"])),None)
            movement = None if historical is None else {"record_id":historical["record_id"],"state":"held","delta":historical["cash_delta_cents"]}
        if not qualified or state not in {"held","cleared"} or delta is None or cash_id is None or movement is None or movement["state"] not in ({"held","cleared"} if state == "held" else {"cleared"}) or movement["delta"] != delta:
            case["unknown_cash_events"] += 1
            case["reason_codes"].append("cash_reference_unreconciled:"+identity)
            continue
        if cash_id in cash_owners and cash_owners[cash_id] != event["case_id"]:
            case["unknown_cash_events"] += 1
            case["reason_codes"].append("cash_consumption_belongs_to_another_case:"+cash_id)
            continue
        if state == "cleared" and len(allocations.get(cash_id,())) > 1:
            case["reason_codes"].append("cash_reference_allocated_more_than_once:"+cash_id)
            case["unknown_cash_events"] += 1
            continue
        cash_record = resolve(movement["record_id"])
        if cash_record is None:
            case["unknown_cash_events"] += 1
            case["reason_codes"].append("cash_source_record_missing:"+identity)
            continue
        cash_check = subject_claims(cash_record["basis_claim_ids"],cash_record,as_of=as_of,resolve=resolve,verify_claim=verify_claim)
        case["checks"]["cash:"+cash_id+":"+state] = cash_check
        if cash_check["state"] != "pass" or not {"scope","operation","quantities","cash_event_id","cash_delta_cents","cash_state","occurred_at"}.issubset(cash_check["verified_fields"]):
            case["unknown_cash_events"] += 1
            case["reason_codes"].append("cash_source_record_unverified:"+identity)
            continue
        if state == "held":
            case.setdefault("verified_held_cash_ids",[]).append(cash_id)
        else:
            case.setdefault("cleared_cash_ids",[]).append(cash_id)
            case["known_cleared_cash_delta_cents"] += delta
    for case_id,case in cases.items():
        consumed_minutes = 0
        for consumption in consumptions.get(case_id,[]):
            identity = consumption["record_id"]
            consumed_minutes += consumption["quantities"]["work_minutes"]
            case.setdefault("consumption_digests",{})[identity] = record_digest(consumption)
            check = subject_claims(consumption["basis_claim_ids"],consumption,as_of=as_of,resolve=resolve,verify_claim=verify_claim)
            case["checks"]["consumption:"+identity] = check
            if check["state"] != "pass" or not {"scope","reservation_id","quantities","cash_event_id","cash_delta_cents","occurred_at"}.issubset(check["verified_fields"]):
                case["reason_codes"].append("case_consumption_basis_unverified:"+identity)
            if consumption["cash_event_id"] is not None and consumption["cash_event_id"] not in case.get("cleared_cash_ids",[]):
                case["unknown_cash_events"] += 1
                case["reason_codes"].append("case_consumption_outcome_missing:"+identity)
        case["ledger_consumed_work_minutes"] = consumed_minutes
        if consumed_minutes > case["known_operator_minutes"]:
            case["unknown_time_events"] += 1
            case["reason_codes"].append("case_consumed_time_not_fully_observed")
        case["held_cash_ids"] = sorted(set(case["held_cash_ids"]))
        amounts = []
        for identity in case["held_cash_ids"]:
            movement = cash_account.get("cash_events",{}).get(identity)
            if movement is None or identity not in case.get("verified_held_cash_ids",[]):
                amounts.append(None)
            elif movement["state"] == "held":
                amounts.append(movement["delta"])
            elif identity not in case.get("cleared_cash_ids",[]):
                amounts.append(None)
                case["unknown_cash_events"] += 1
                case["reason_codes"].append("cleared_payout_allocation_missing:"+identity)
        case["held_receivable_cents"] = sum(amounts) if all(amount is not None for amount in amounts) else None
        case["cash_flow_resolved"] = not case["unknown_cash_events"] and not case["reason_codes"]
        case["measurement_complete"] = case["cash_flow_resolved"] and not case["unknown_time_events"]
        case["cleared_cash_delta_cents"] = case["known_cleared_cash_delta_cents"] if case["cash_flow_resolved"] else None
        case["realized_profit_cents"] = None
        case["profit_status"] = "full_cost_accounting_required"
        case["material_sha256"] = digest(case,domain="projection")
    return dict(active,cases=cases,input_digests={record["record_id"]:record_digest(record) for record in active["active"]})
