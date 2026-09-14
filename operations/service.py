"""Readiness orchestration over current local evidence; no external actions."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal

from . import capacity, store
from .bindings import subject_claims
from .clock import parse_timestamp, utc_now
from .contracts import read_record, record_digest
from .economics import assess_scenario
from .evidence_adapter import adapt_events
from .errors import ContractError, IntegrityError, OperationsError, UnresolvedError
from .lineage import group_events
from .readiness import compose
from .routes import assess_route
from .serialization import canonical_bytes, digest, load_json
from .sources import SCOPE_FIELDS, assess_source
from .upstream import read_result, recheck
from .verification import read_verification


def _check(function):
    try:
        return function()
    except OperationsError as exc:
        return {"state":"fail" if isinstance(exc,IntegrityError) else "unknown","reason_codes":[exc.code+":"+str(exc)]}


def _combine(checks):
    states = {check["state"] for check in checks}
    return {"state":"fail" if "fail" in states else "unknown" if not checks or "unknown" in states else "pass","reason_codes":[code for check in checks for code in check.get("reason_codes",[])],"checks":checks}


def _assess(connection, request, *, as_of, verify_claim=None, verify_edge=None):
    request = read_record(request,expected_type="readiness_request")
    now = parse_timestamp(as_of)
    if parse_timestamp(request["created_at"]) > now or parse_timestamp(request["valid_until"]) <= now:
        raise UnresolvedError("Readiness request is not current")
    events = store._events(connection)
    records = {record["record_id"]:record for _,record in events}
    inputs, verifications = {}, {}
    expiry = [parse_timestamp(request["valid_until"])]

    def resolve(identity):
        record = records.get(identity)
        if record is not None:
            inputs[identity] = record_digest(record)
            if record.get("valid_until"):
                expiry.append(parse_timestamp(record["valid_until"]))
            if record.get("requirement") == "required" and record.get("effective_deadline_at"):
                expiry.append(parse_timestamp(record["effective_deadline_at"]))
        return record

    def get(identity,kind):
        record = resolve(identity)
        if record is None:
            raise UnresolvedError("Missing readiness input: "+identity)
        record = read_record(record,expected_type=kind)
        if record["synthetic"] != request["synthetic"]:
            raise IntegrityError("Readiness cannot mix synthetic and real records")
        if parse_timestamp(record["created_at"]) > now:
            raise UnresolvedError("Readiness input not available yet: "+identity)
        if record.get("valid_until"):
            until = parse_timestamp(record["valid_until"])
            if until <= now:
                raise UnresolvedError("Readiness input expired: "+identity)
            expiry.append(until)
        return record

    def verify(identity,**kwargs):
        if identity not in verifications:
            claim = get(identity,"material_claim")
            checked = read_verification(verify_claim(identity,as_of=as_of,synthetic=request["synthetic"])) if verify_claim else {"state":"unknown","reason_codes":["qualified_verifier_unavailable"]}
            if checked["state"] == "pass" and (checked.get("claim_id") != identity or checked.get("claim_sha256") != record_digest(claim) or checked.get("subject") != claim["subject"]):
                raise IntegrityError("Qualified verifier differs from its stored claim")
            verifications[identity] = checked
        return deepcopy(verifications[identity])

    upstream = get(request["upstream_evaluation_id"],"upstream_evaluation")
    scenario = get(request["operating_review_id"],"operating_review")
    policy = get(request["policy_id"],"operating_policy")
    route = get(request["route_id"],"transaction_route")
    actor = get(request["actor_ref"],"actor_authority")
    for selected,scope_field in ((route,"case_id"),(scenario,"case_id"),(policy,"scope")):
        revisions = [record for _,record in events if record["record_type"] == selected["record_type"] and record["synthetic"] == selected["synthetic"] and record[scope_field] == selected[scope_field] and parse_timestamp(record["created_at"]) <= now]
        if revisions[-1]["record_id"] != selected["record_id"]:
            raise UnresolvedError("A later operative input requires a new readiness request")
    for record in (upstream,scenario,route):
        if record["case_id"] != request["case_id"]:
            raise IntegrityError("Readiness input belongs to another case")
    if policy["scope"] != request["scope"] or scenario["scope"] != request["scope"]:
        raise IntegrityError("Readiness policy/scenario scope differs")
    upstream_result = read_result(upstream)
    evidence = [get(identity,"evidence_event") for identity in request["evidence_event_ids"]]
    edges = [get(identity,"lineage_edge") for identity in request["lineage_edge_ids"]]
    # A frozen request cannot hide a later refund, copy, or disputed lineage edge.
    # Expand its known event/lineage component from the current journal.
    identities = {item["record_id"] for item in evidence}
    event_ids = {item["event_id"] for item in evidence if item["event_id"] is not None}
    changed = True
    while changed:
        prior = set(identities)
        for _,record in events:
            if record["synthetic"] != request["synthetic"] or parse_timestamp(record["created_at"]) > now:
                continue
            if record["record_type"] == "evidence_event" and record["event_id"] in event_ids:
                identities.add(record["record_id"])
            elif record["record_type"] == "lineage_edge" and identities.intersection((record["left_evidence_id"],record["right_evidence_id"])):
                identities.update((record["left_evidence_id"],record["right_evidence_id"]))
        for identity in identities:
            item = get(identity,"evidence_event")
            if item["event_id"] is not None:
                event_ids.add(item["event_id"])
        changed = prior != identities
    evidence = [get(identity,"evidence_event") for identity in sorted(identities)]
    edges = [get(record["record_id"],"lineage_edge") for _,record in events if record["record_type"] == "lineage_edge" and record["synthetic"] == request["synthetic"] and {record["left_evidence_id"],record["right_evidence_id"]}.issubset(identities) and parse_timestamp(record["created_at"]) <= now]

    def rights():
        checked = []
        for selected in request["source_requests"]:
            rule = get(selected["rule_id"],"source_rule")
            current_rules = [record for _,record in events if record["record_type"] == "source_rule" and record["synthetic"] == request["synthetic"] and all(record[field] == rule[field] for field in SCOPE_FIELDS) and parse_timestamp(record["created_at"]) <= now]
            if current_rules[-1]["record_id"] != rule["record_id"]:
                raise UnresolvedError("Source permission changed; a new readiness request is required")
            checked.append(assess_source(rule,action=selected["action"],context=selected["context"],as_of=as_of,synthetic=request["synthetic"],resolve=resolve,verify_claim=verify))
        retained = {item["rule_id"] for item in request["source_requests"] if item["action"] == "retention"}
        if not evidence or {item["source_rule_id"] for item in evidence}-retained:
            checked.append({"state":"unknown","reason_codes":["evidence_retention_scope_missing"]})
        return _combine(checked)

    def integrity():
        bound = subject_claims(request["basis_claim_ids"],request,as_of=as_of,resolve=resolve,verify_claim=verify)
        reasons = list(bound["reason_codes"])
        if not (set(request)-{"producer_ref","created_at","record_type","schema_version"}).issubset(bound["verified_fields"]):
            reasons.append("readiness_mapping_and_resource_basis_unverified")
        prices = adapt_events(evidence,as_of=as_of,synthetic=request["synthetic"],verify_claim=verify)
        def checked_edge(edge,**kwargs):
            if verify_edge is None:
                return {"state":"unknown"}
            result = read_verification(verify_edge(edge,as_of=as_of))
            verifications["edge:"+edge["record_id"]] = result
            return result
        grouping = group_events(evidence,edges,as_of=as_of,synthetic=request["synthetic"],verify_edge=checked_edge)
        accepted = {item["evidence_id"]:Decimal(item["item_price_eur"])*100 for item in upstream_result["accepted_comps"]}
        candidates = {item["record_id"]:item["item_price_cents"] for item in prices["candidates"]}
        if not accepted or accepted != candidates or prices["rejected"] or grouping["unresolved_evidence_ids"] or grouping["independent_event_count"] != len(accepted):
            reasons.append("upstream_comparable_event_mapping_unresolved")
        if actor["revoked_at"] or "human_decision:"+request["scope"] not in actor["scopes"]:
            reasons.append("human_actor_scope_unavailable")
        current_actors = [record for _,record in events if record["record_type"] == "actor_authority" and record["synthetic"] == request["synthetic"] and record["identity_ref"] == actor["identity_ref"] and "human_decision:"+request["scope"] in record["scopes"] and parse_timestamp(record["created_at"]) <= now]
        if current_actors and (current_actors[-1]["record_id"] != actor["record_id"] or any(record["revoked_at"] is not None for record in current_actors)):
            reasons.append("human_actor_authority_changed")
        grants = subject_claims(actor["grant_claim_ids"],actor,as_of=as_of,resolve=resolve,verify_claim=verify)
        if grants["state"] != "pass" or not {"identity_ref","scopes","effective_at","valid_until"}.issubset(grants["verified_fields"]):
            reasons.append("human_actor_grant_unverified")
        if parse_timestamp(actor["effective_at"]) > now:
            reasons.append("human_actor_not_effective")
        grouping = {key:value for key,value in grouping.items() if key not in {"as_of","grouping_sha256"}}
        return {"state":"fail" if bound["state"] == "fail" else "unknown" if reasons else "pass","reason_codes":reasons,"binding":bound,"grouping":grouping}

    def current():
        try:
            result = recheck(upstream)
        except ContractError as exc:
            raise IntegrityError("Previously bound evaluator input no longer satisfies its contract") from exc
        if policy["validity_seconds"] is None:
            raise UnresolvedError("Current upstream validity is unspecified")
        until = parse_timestamp(upstream["created_at"])+timedelta(seconds=policy["validity_seconds"])
        if now >= until or now.date() != parse_timestamp(upstream["as_of"]).date():
            raise UnresolvedError("Current upstream evaluation is stale")
        expiry.extend((until,now.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)))
        return {"state":{"supported":"pass","unsupported":"fail","unresolved":"unknown"}[result["outcome"]],"reason_codes":list(result["reasons"]),"result_sha256":digest(result)}

    def economics():
        result = assess_scenario(scenario,policy,upstream,resolve=resolve,verify_claim=verify)
        arithmetic = upstream_result["arithmetic"]
        if arithmetic is None or Decimal(arithmetic["acquisition_price_eur"])*100 != scenario["acquisition_cents"] or route["acquisition_cents"] != scenario["acquisition_cents"]:
            raise IntegrityError("Acquisition differs across current evaluation, scenario and route")
        for line in scenario["cost_lines"]:
            if line["effective_until"]:
                until = parse_timestamp(line["effective_until"])
                if until <= now:
                    raise UnresolvedError("Operating cost basis expired")
                expiry.append(until)
            if line["cost_policy_id"]:
                get(line["cost_policy_id"],"cost_policy")
        return result

    def resources():
        account = capacity.reduce_resources(events).get((request["scope"],request["synthetic"]),capacity._new_account())
        result = capacity._qualified_view(account,events,as_of=as_of,verify_claim=verify)
        target = result["reservations"].get(request["reservation_id"])
        if target is None or target["state"] != "tentative" or target["case_id"] != request["case_id"]:
            raise UnresolvedError("Current case has no tentative reservation")
        expiry.append(parse_timestamp(target["expires_at"]))
        requested = request["resource_requirements"]
        if any(target["remaining"][key] < requested[key] for key in capacity.RESOURCES):
            raise IntegrityError("Reservation does not cover reviewed resource requirements")
        cash = [scenario["acquisition_cents"]]+[line["amount_cents"] for line in scenario["cost_lines"] if line["treatment"] in {"cash","reserve"}]
        if any(value is None for value in cash) or requested["cash_cents"] < sum(cash):
            raise UnresolvedError("Reservation does not cover the full cash-cost and reserve scenario")
        inputs.update(result.get("input_digests",{}))
        if account["snapshot"]:
            expiry.append(parse_timestamp(account["snapshot"]["valid_until"]))
        return result

    checks = {"rights":_check(rights),"integrity":_check(integrity),"current_economics":_check(current),"operating_scenario":_check(economics),"route":_check(lambda:assess_route(route,case_id=request["case_id"],as_of=as_of,synthetic=request["synthetic"],resolve=resolve,verify_claim=verify)),"resources":_check(resources)}
    # Resolver-dependent facts and verification responses are part of the bound
    # material, even if a subservice has a smaller result/provenance envelope.
    material = {"request_sha256":record_digest(request),"inputs":inputs,"verifications":{identity:digest(value,domain="projection") for identity,value in verifications.items()},"checks":checks}
    result = compose(checks)
    future_expiry = [value for value in expiry if value > now]
    return dict(result,material=material,binding_sha256=digest(material,domain="projection"),valid_until=min(future_expiry).isoformat().replace("+00:00","Z"),input_record_ids=sorted(inputs),input_digests=sorted(set(inputs.values())))


def assess(path, request, *, verify_claim=None, verify_edge=None):
    """Read-only assessment; a caller-supplied request has no approval authority."""
    with store._connection(path) as connection:
        return _assess(connection,request,as_of=utc_now(),verify_claim=verify_claim,verify_edge=verify_edge)


def prepare(path, request, *, record_id, request_key, verify_claim=None, verify_edge=None):
    request = read_record(request,expected_type="readiness_request")
    with store._connection(path,write=True) as connection:
        # A retry compares the immutable intent, then returns its original snapshot.
        old = connection.execute("SELECT record_id FROM request_receipts WHERE request_key=?",(request_key,)).fetchone()
        if old:
            saved = store._validated_row(connection.execute("SELECT * FROM records WHERE record_id=?",(old[0],)).fetchone())
            if saved["record_type"] != "readiness_snapshot" or saved["record_id"] != record_id or load_json(saved["material_json"])["request_sha256"] != record_digest(request):
                raise IntegrityError("Readiness retry differs from the original intent")
            return store._append(connection,saved,request_key=request_key)
        now = utc_now()
        result = _assess(connection,request,as_of=now,verify_claim=verify_claim,verify_edge=verify_edge)
        finished = utc_now()
        if parse_timestamp(finished) < parse_timestamp(now) or parse_timestamp(finished) >= parse_timestamp(result["valid_until"]):
            raise UnresolvedError("Readiness expired or the clock moved backwards during assessment")
        record = {"record_type":"readiness_snapshot","schema_version":1,"record_id":record_id,"created_at":finished,"synthetic":request["synthetic"],"producer_ref":request["actor_ref"],"case_id":request["case_id"],"scope":request["scope"],"as_of":now,"policy_id":request["policy_id"],"request_id":request["record_id"],"actor_ref":request["actor_ref"],"reservation_id":request["reservation_id"],"material_json":canonical_bytes(result.pop("material")).decode("utf-8"),**result}
        if records_request := connection.execute("SELECT * FROM records WHERE record_id=?",(request["record_id"],)).fetchone():
            if store._validated_row(records_request) != request:
                raise IntegrityError("Readiness request differs from retained record")
        else:
            raise UnresolvedError("Retain the reviewed readiness request before preparing its snapshot")
        return store._append(connection,read_record(record),request_key=request_key)
