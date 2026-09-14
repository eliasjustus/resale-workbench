"""Private concentration, forward-exit review and authenticated intake controls."""
from . import capacity, decisions, store
from .bindings import subject_claims
from .clock import parse_timestamp, utc_now
from .contracts import read_record, record_digest
from .errors import IntegrityError, UnresolvedError
from .exit_review import assess as assess_exit
from .outcomes import summarize_outcomes
from .serialization import canonical_bytes, digest

DIMENSIONS = ("provider_ref","account_ref","source_ref","category","correlated_failure_ref")


def intake_state(events,*,scope,synthetic,as_of):
    controls = [record for _,record in events if record["record_type"] == "portfolio_decision" and record["decision"] in {"pause_intake","resume_intake"} and record["scope"] == scope and record["synthetic"] == synthetic and parse_timestamp(record["created_at"]) <= parse_timestamp(as_of)]
    return {"paused":bool(controls and controls[-1]["decision"] == "pause_intake"),"decision_id":controls[-1]["record_id"] if controls else None,"decision_sha256":record_digest(controls[-1]) if controls else None}


def _view(events,*,scope,synthetic,as_of,verify_claim):
    records = [record for _,record in events]
    by_id = {record["record_id"]:record for record in records}
    account = capacity.reduce_resources(events).get((scope,synthetic),capacity._new_account())
    resources = capacity._qualified_view(account,events,as_of=as_of,verify_claim=verify_claim,for_intake=False)
    cases = {}
    for cohort_id in {record["cohort_id"] for record in records if record["record_type"] == "outcome_event" and record["scope"] == scope and record["synthetic"] == synthetic}:
        outcomes = summarize_outcomes(records,as_of=as_of,cohort_id=cohort_id,synthetic=synthetic,scope=scope,cash_account=account,resolve=by_id.get,verify_claim=verify_claim)
        for identity,case in outcomes["cases"].items():
            cases[identity] = {"physical_state":case["physical_state"],"financial_state":case["financial_state"],"outcome_material_sha256":case["material_sha256"],"held_receivable_cents":case["held_receivable_cents"],"outcome_measurement_complete":case["measurement_complete"]}
    for reservation_id,reservation in resources["reservations"].items():
        case = cases.setdefault(reservation["case_id"],{"physical_state":"unknown","financial_state":"unknown"})
        case.setdefault("reservation_ids",[]).append(reservation_id)
        case["occupied_units"] = case.get("occupied_units",0)+reservation["occupied_units"]
        case["committed"] = case.get("committed",False) or reservation["state"] == "committed"
    for attempt in decisions._manual_attempts(events).values():
        if attempt["scope"] == scope and attempt["synthetic"] == synthetic and attempt["state"] in {"preflight_passed","outcome_unknown"}:
            reservation = resources["reservations"].get(attempt["reservation_id"])
            if reservation:
                case = cases.setdefault(reservation["case_id"],{"physical_state":"unknown","financial_state":"unknown"})
                case.setdefault("unreconciled_attempt_ids",[]).append(attempt["record_id"])
    exposures = {}
    for record in records:
        if record["record_type"] == "portfolio_case" and record["scope"] == scope and record["synthetic"] == synthetic and parse_timestamp(record["created_at"]) <= parse_timestamp(as_of):
            exposures[record["case_id"]] = record
            cases.setdefault(record["case_id"],{"physical_state":"unknown","financial_state":"unknown"})
    groups = {field:{} for field in DIMENSIONS}
    for identity,case in cases.items():
        exposure = exposures.get(identity)
        qualified = False
        if exposure:
            check = subject_claims(exposure["basis_claim_ids"],exposure,as_of=as_of,resolve=by_id.get,verify_claim=verify_claim)
            case["exposure_basis"] = check
            case["exposure_sha256"] = record_digest(exposure)
            qualified = check["state"] == "pass" and set(exposure).issubset(check["verified_fields"])
        for field in DIMENSIONS:
            value = exposure[field] if qualified and exposure[field] is not None else "unresolved"
            groups[field].setdefault(value,[]).append(identity)
        case["aftercare_status"] = "case_followup_review_required"
    control = intake_state(events,scope=scope,synthetic=synthetic,as_of=as_of)
    # As-of time controls qualification, but passing seconds alone do not change
    # a human's material review. Revisions and proof/expiry changes still do.
    resource_material = {key:value for key,value in resources.items() if key != "as_of"}
    result = {"scope":scope,"synthetic":synthetic,"resources":resources,"intake":control,"new_intake_allowed":not control["paused"] and resources["state"] == "pass","cases":cases,"concentration":groups,"aftercare_stopped":False,"all_work_complete":False,"purchase_authorized":False}
    return dict(result,material_sha256=digest(dict(result,resources=resource_material),domain="projection"))


def status(path,*,scope,synthetic,verify_claim=None):
    with store._connection(path) as connection:
        return _view(store._events(connection),scope=scope,synthetic=synthetic,as_of=utc_now(),verify_claim=verify_claim)


def record_decision(path,record,*,context,request_key,verify_claim=None):
    record = read_record(record,expected_type="portfolio_decision")
    if record["request_sha256"] is not None or record["request_json"] is not None:
        raise IntegrityError("Request receipt fields are assigned by the authenticated writer")
    with store._connection(path,write=True) as connection:
        events = store._events(connection)
        now = utc_now()
        actor,_ = decisions._context(context,events,action="portfolio_control",as_of=now,verify_claim=verify_claim)
        if (record["actor_ref"],record["scope"],record["synthetic"]) != (context.actor_ref,context.scope,context.synthetic) or parse_timestamp(record["created_at"]) > parse_timestamp(now):
            raise IntegrityError("Portfolio decision differs from authenticated context")
        retry = decisions._retry(connection,request_key,record,record["record_id"],"portfolio_decision")
        if retry is not None:
            return retry
        current = _view(events,scope=context.scope,synthetic=context.synthetic,as_of=now,verify_claim=verify_claim)
        if (record["portfolio_material_sha256"],record["expected_resource_revision"]) != (current["material_sha256"],current["resources"]["revision"]):
            raise UnresolvedError("Portfolio review material changed")
        resolve = {item["record_id"]:item for _,item in events}.get
        if record["decision"] == "resume_intake":
            check = subject_claims(record["basis_claim_ids"],record,as_of=now,resolve=resolve,verify_claim=verify_claim)
            if current["resources"]["state"] != "pass" or check["state"] != "pass" or not set(record).issubset(check["verified_fields"]):
                raise UnresolvedError("Resume requires qualified resources and reviewed scope")
        if record["decision"] == "record_exit_choice":
            review = resolve(record["exit_review_id"])
            if review is None:
                raise UnresolvedError("Exit review is missing")
            review = read_record(review,expected_type="exit_review")
            if review["case_id"] not in current["cases"]:
                raise UnresolvedError("Exit review case is absent from the portfolio")
            if record_digest(review) != record["exit_review_sha256"] or review["scope"] != context.scope or review["synthetic"] != context.synthetic:
                raise IntegrityError("Exit choice differs from the reviewed scope")
            assessment = assess_exit(review,as_of=now,resolve=resolve,verify_claim=verify_claim)
            if record["selected_option_id"] not in assessment["alternatives"] or assessment["alternatives"][record["selected_option_id"]]["state"] != "pass":
                raise UnresolvedError("Exit alternative is not qualified")
        elif any(record[field] is not None for field in ("exit_review_id","exit_review_sha256","selected_option_id")):
            raise IntegrityError("Intake controls cannot contain an exit choice")
        recorded_at = utc_now()
        finished = parse_timestamp(recorded_at)
        deadlines = [actor["valid_until"]]
        if record["decision"] == "resume_intake":
            snapshot = resolve(current["resources"]["snapshot_id"])
            deadlines.extend((snapshot["valid_until"],resolve(snapshot["policy_id"])["valid_until"]))
        if record["decision"] == "record_exit_choice":
            deadlines.append(review["valid_until"])
        if finished < parse_timestamp(now) or any(finished >= parse_timestamp(deadline) for deadline in deadlines):
            raise UnresolvedError("Portfolio authority expired during review")
        retained = dict(record,created_at=recorded_at,request_sha256=digest(record,domain="request"),request_json=canonical_bytes(record).decode("utf-8"))
        return store._append(connection,retained,request_key=request_key)
