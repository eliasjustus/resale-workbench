"""Explicit lead inclusion, immutable follow-up and private as-of cohort views."""
from . import capacity, store
from .bindings import subject_claims
from .clock import parse_timestamp, utc_now
from .contracts import read_record, record_digest
from .errors import IntegrityError, UnresolvedError
from .funnel import summarize_funnel
from .outcomes import summarize_outcomes
from .serialization import canonical_bytes, digest


def _definition(records,identity):
    definition = next((record for record in records if record["record_id"] == identity),None)
    if definition is None:
        raise UnresolvedError("Cohort definition is missing")
    return read_record(definition,expected_type="cohort_definition")


def include(path,inclusion,*,request_key):
    inclusion = read_record(inclusion,expected_type="cohort_inclusion")
    with store._connection(path,write=True) as connection:
        if connection.execute("SELECT 1 FROM request_receipts WHERE request_key=?",(request_key,)).fetchone():
            return store._append(connection,inclusion,request_key=request_key)
        records = [record for _,record in store._events(connection)]
        definition = _definition(records,inclusion["cohort_id"])
        if (inclusion["cohort_sha256"],inclusion["scope"],inclusion["synthetic"]) != (record_digest(definition),definition["scope"],definition["synthetic"]):
            raise IntegrityError("Inclusion differs from its frozen cohort definition")
        observed = parse_timestamp(inclusion["observed_at"])
        if parse_timestamp(definition["frozen_at"]) > observed or parse_timestamp(definition["created_at"]) > observed or observed < parse_timestamp(definition["intake_started_at"]) or definition["intake_ended_at"] is not None and observed > parse_timestamp(definition["intake_ended_at"]):
            raise IntegrityError("Lead lies outside the predefined inclusion window")
        if parse_timestamp(inclusion["created_at"]) > parse_timestamp(utc_now()):
            raise UnresolvedError("Inclusion cannot be recorded in the future")
        existing = [record for record in records if record["record_type"] == "cohort_inclusion" and record["cohort_id"] == inclusion["cohort_id"]]
        if any(record["record_type"] == "cohort_inclusion" and record["case_id"] == inclusion["case_id"] for record in records):
            raise IntegrityError("Case already has a cohort owner")
        if inclusion["position"] != len(existing)+1:
            raise IntegrityError("Known lead inclusion sequence must remain consecutive")
        if any(record["case_id"] == inclusion["case_id"] or record["lead_id"] == inclusion["lead_id"] for record in existing):
            raise IntegrityError("Lead/case already belongs to this cohort")
        if existing and observed < max(parse_timestamp(record["observed_at"]) for record in existing):
            raise IntegrityError("Lead inclusion cannot reorder already observed consecutive leads")
        return store._append(connection,inclusion,request_key=request_key)


def record_followup(path,followup,*,request_key):
    followup = read_record(followup,expected_type="case_followup")
    with store._connection(path,write=True) as connection:
        if connection.execute("SELECT 1 FROM request_receipts WHERE request_key=?",(request_key,)).fetchone():
            return store._append(connection,followup,request_key=request_key)
        events = store._events(connection)
        if not any(record["record_type"] == "cohort_inclusion" and all(record[field] == followup[field] for field in ("cohort_id","case_id","scope","synthetic")) for _,record in events):
            raise IntegrityError("Follow-up must belong to an included case")
        if parse_timestamp(followup["created_at"]) > parse_timestamp(utc_now()):
            raise UnresolvedError("Follow-up cannot be recorded in the future")
        return store._append(connection,followup,request_key=request_key)


def report(path,cohort_id,*,as_of,verify_claim=None,_include_accounting=True):
    now = parse_timestamp(as_of)
    with store._connection(path) as connection:
        events = [(sequence,record) for sequence,record in store._events(connection) if parse_timestamp(record["created_at"]) <= now]
        records = [record for _,record in events]
        definition = _definition(records,cohort_id)
        by_id = {record["record_id"]:record for record in records}
        inclusions = [record for record in records if record["record_type"] == "cohort_inclusion" and record["cohort_id"] == cohort_id and parse_timestamp(record["observed_at"]) <= now]
        if [record["position"] for record in inclusions] != list(range(1,len(inclusions)+1)):
            raise IntegrityError("Cohort inclusion sequence is inconsistent at this view")
        qualified_definition = subject_claims(definition["basis_claim_ids"],definition,as_of=as_of,resolve=by_id.get,verify_claim=verify_claim)
        account = capacity.reduce_resources(events).get((definition["scope"],definition["synthetic"]),capacity._new_account())
        outcomes = summarize_outcomes(records,as_of=as_of,cohort_id=cohort_id,synthetic=definition["synthetic"],scope=definition["scope"],cash_account=account,resolve=by_id.get,verify_claim=verify_claim)
        included_cases = {record["case_id"] for record in inclusions}
        orphaned = sorted(set(outcomes["cases"])-included_cases)
        cases,cutoff_reports = {},{}
        for inclusion in inclusions:
            if inclusion["cohort_sha256"] != record_digest(definition) or inclusion["scope"] != definition["scope"] or inclusion["synthetic"] != definition["synthetic"]:
                raise IntegrityError("Retained inclusion differs from cohort definition")
            case = outcomes["cases"].get(inclusion["case_id"],{"event_ids":[],"event_types":[],"physical_state":"unknown","financial_state":"unknown","known_operator_minutes":0,"unknown_time_events":1,"known_cleared_cash_delta_cents":0,"unknown_cash_events":1,"cleared_cash_delta_cents":None,"held_receivable_cents":None,"realized_profit_cents":None,"profit_status":"full_cost_accounting_required","reason_codes":["case_outcome_not_observed"],"last_occurred_at":None})
            case["inclusion_id"] = inclusion["record_id"]
            case["inclusion_position"] = inclusion["position"]
            case["inclusion_check"] = subject_claims(inclusion["basis_claim_ids"],inclusion,as_of=as_of,resolve=by_id.get,verify_claim=verify_claim)
            case["outcome_material_sha256"] = digest({"outcomes":case.get("material_sha256",digest(case,domain="projection")),"inclusion_sha256":record_digest(inclusion),"inclusion_check":case["inclusion_check"],"cohort_sha256":record_digest(definition),"cohort_check":qualified_definition},domain="projection")
            followups = [record for record in records if record["record_type"] == "case_followup" and record["cohort_id"] == cohort_id and record["case_id"] == inclusion["case_id"]]
            case["followup_status"] = "missing"
            if followups:
                followup = followups[-1]
                if followup["synthetic"] != definition["synthetic"] or followup["scope"] != definition["scope"]:
                    raise IntegrityError("Follow-up differs from cohort scope")
                check = subject_claims(followup["basis_claim_ids"],followup,as_of=as_of,resolve=by_id.get,verify_claim=verify_claim)
                case["followup_check"] = check
                case["followup_id"] = followup["record_id"]
                case["followup_limitations"] = followup["limitations"]
                case["followup_status"] = "censored" if followup["coverage"] == "censored" else "incomplete"
                required = {"cohort_id","case_id","scope","as_of","required_followup_until","physical_closed","financial_closed","aftercare_closed","coverage","outcome_material_sha256"}
                closed = check["state"] == "pass" and required.issubset(check["verified_fields"]) and followup["coverage"] == "complete" and all(followup[field] is True for field in ("physical_closed","financial_closed","aftercare_closed")) and followup["required_followup_until"] is not None and parse_timestamp(followup["required_followup_until"]) <= parse_timestamp(followup["as_of"]) <= now
                if case["last_occurred_at"] is not None and parse_timestamp(case["last_occurred_at"]) > parse_timestamp(followup["as_of"]):
                    closed = False
                    case["reason_codes"].append("new_outcome_after_followup")
                if followup["outcome_material_sha256"] != case["outcome_material_sha256"]:
                    closed = False
                    case["reason_codes"].append("followup_material_changed")
                if not case.get("cash_flow_resolved",False):
                    closed = False
                    case["reason_codes"].append("followup_cash_flow_unresolved")
                if not case.get("measurement_complete",False):
                    closed = False
                    case["reason_codes"].append("followup_measurement_incomplete")
                if case["physical_state"] not in {"not_acquired","delivered","disposed"} or case["financial_state"] not in {"no_transaction","settled","refunded","claim_closed"}:
                    closed = False
                if any(item["case_id"] == inclusion["case_id"] and (item["state"] == "committed" or item["occupied_units"] > 0) for item in account["reservations"].values()):
                    closed = False
                    case["reason_codes"].append("case_resources_still_committed")
                if case["held_receivable_cents"] is None or case["held_receivable_cents"] > 0:
                    closed = False
                if closed:
                    case["followup_status"] = "closed"
            case["sale_price_cents"] = None  # A missing/open sale is never zero.
            accounting_reviews = [record for record in records if record["record_type"] == "realized_cost_review" and record["case_id"] == inclusion["case_id"] and record["cohort_id"] == cohort_id]
            if accounting_reviews and _include_accounting:
                from .realized import assess
                review = accounting_reviews[-1]
                if parse_timestamp(review["as_of"]) == now:
                    cutoff_case = case
                elif parse_timestamp(review["as_of"]) > now:
                    cutoff_case = None
                else:
                    if review["as_of"] not in cutoff_reports:
                        try:
                            cutoff_reports[review["as_of"]] = report(path,cohort_id,as_of=review["as_of"],verify_claim=verify_claim,_include_accounting=False)["cases"]
                        except UnresolvedError:
                            cutoff_reports[review["as_of"]] = {}
                    cutoff_case = cutoff_reports[review["as_of"]].get(inclusion["case_id"])
                accounting = assess(review,case,cutoff_case=cutoff_case,cohort_check=qualified_definition,cohort_id=cohort_id,scope=definition["scope"],synthetic=definition["synthetic"],as_of=as_of,cash_account=account,resolve=by_id.get,verify_claim=verify_claim)
                case["realized_accounting"] = accounting
                case["realized_profit_cents"] = accounting["realized_contribution_cents"]
                case["profit_status"] = "qualified_fixed_cost_contribution" if accounting["state"] == "pass" else "full_cost_accounting_unresolved"
            cases[inclusion["case_id"]] = case
        result = {"cohort_id":cohort_id,"cohort_sha256":record_digest(definition),"sampling_kind":definition["sampling_kind"],"as_of":as_of,"definition_check":qualified_definition,"cases":cases,"funnel":summarize_funnel(cases),"orphan_outcome_case_ids":orphaned,"superseded_event_ids":outcomes["superseded_ids"],"source_completeness_status":"requires_independent_intake_frame_review","claim_profitable_operation":False,"purchase_authorized":False}
        return dict(result,report_sha256=digest(result,domain="projection"))


def export_report(path,cohort_id,destination,*,as_of,verify_claim=None):
    result = report(path,cohort_id,as_of=as_of,verify_claim=verify_claim)
    destination = store._local_path(destination)
    if destination.exists():
        raise IntegrityError("Cohort export destination must be new")
    with destination.open("xb") as stream:
        stream.write(canonical_bytes(result))
    return destination
