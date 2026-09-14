"""Atomic resource holds over the existing immutable local event journal."""
from copy import deepcopy
from datetime import timedelta

from . import store
from .bindings import subject_claims
from .clock import parse_timestamp, utc_now
from .contracts import read_record, record_digest
from .errors import ConflictError, IntegrityError, UnresolvedError
from .policies import assess_operating_policy
from .serialization import canonical_bytes, digest
from .verification import read_verification

RESOURCES = ("cash_cents","work_minutes","storage_units")
ACTION_FIELDS = {"scope","reservation_id","operation","quantities","cash_event_id","cash_delta_cents","cash_state","occurred_at","work_period"}


def _new_account():
    return {"revision":0,"snapshot":None,"reservations":{},"cash_events":{},"allocated_cash_ids":[],"spent_work":{},"work_periods":[]}


def _action_shape(record):
    operation, quantities = record["operation"], record["quantities"]
    cash_use = operation == "consume" and quantities["cash_cents"] > 0
    if operation != "cash" and not cash_use:
        if record["cash_event_id"] is not None or record["cash_delta_cents"] is not None or record["cash_state"] != "not_applicable":
            raise IntegrityError("Non-cash action carries unused cash fields")
    if cash_use and (record["cash_delta_cents"] != -quantities["cash_cents"] or record["cash_state"] != "cleared"):
        raise IntegrityError("Cash consumption must bind its actual cleared debit")
    if (record["work_period"] is not None) != (operation == "consume" and quantities["work_minutes"] > 0):
        raise IntegrityError("Work consumption must identify its dated budget period")
    if operation in {"commit","release"} and record["expected_resource_revision"] is None:
        raise IntegrityError("Commitment and closeout require the current resource revision")
    if parse_timestamp(record["occurred_at"]) > parse_timestamp(record["created_at"]):
        raise IntegrityError("Resource fact cannot occur after it was recorded")


def _reservation_state(reservation, now):
    if reservation["state"] == "tentative" and parse_timestamp(reservation["expires_at"]) <= now:
        return "expired"
    return reservation["state"]


def _cash(account, record, *, consumption=False):
    identity = record["cash_event_id"]
    if identity is None:
        raise IntegrityError("A stable external cash-event identity is required")
    delta = -record["quantities"]["cash_cents"] if consumption else record["cash_delta_cents"]
    state = "cleared" if consumption else record["cash_state"]
    if state not in {"held","cleared"}:
        raise IntegrityError("Cash event must distinguish held from cleared")
    if state == "held" and delta is not None and delta < 0:
        raise IntegrityError("Held payouts are receivables; actual debits must be recorded as cleared cash movements")
    previous = account["cash_events"].get(identity)
    if previous and previous["state"] == "cleared":
        if not consumption or state != "cleared" or delta != previous["delta"] or parse_timestamp(record["occurred_at"]) != parse_timestamp(previous["occurred_at"]):
            raise ConflictError("Cash identity is already cleared; record a distinct audited correction, not a duplicate")
    else:
        account["cash_events"][identity] = {"state":state,"delta":delta,"occurred_at":record["occurred_at"],"record_id":record["record_id"]}
    if consumption:
        if identity in account["allocated_cash_ids"]:
            raise ConflictError("Cash event already consumed a reservation allocation")
        account["allocated_cash_ids"].append(identity)


def reduce_resources(events):
    """Pure replay: projections are disposable, facts and commitments are not."""
    accounts = {}
    for _, record in events:
        kind = record["record_type"]
        if kind not in store.RESOURCE_RECORDS:
            continue
        key = (record["scope"],record["synthetic"])
        account = accounts.setdefault(key,_new_account())
        expected = record["expected_resource_revision"]
        if expected is not None and expected != account["revision"]:
            raise IntegrityError("Resource event revision is stale or out of sequence")
        now = parse_timestamp(record["created_at"])
        if kind == "capacity_snapshot":
            if record["reflected_event_ids"]:
                raise IntegrityError("Legacy outcome IDs require an explicit stable cash-ID reconciliation")
            previous = account["snapshot"]
            if previous and (record["revision"] <= previous["revision"] or parse_timestamp(record["as_of"]) < parse_timestamp(previous["as_of"])):
                raise IntegrityError("Capacity statements must advance revision and never move backwards in time")
            if previous and not set(previous["reflected_cash_ids"]).issubset(record["reflected_cash_ids"]):
                raise IntegrityError("Statement reconciliation cannot forget already reflected cash IDs")
            account["snapshot"] = deepcopy(record)
            if record["work_period"] not in account["work_periods"]:
                account["work_periods"].append(record["work_period"])
        elif account["snapshot"] is None:
            raise IntegrityError("Resource action has no capacity snapshot")
        elif kind == "reservation":
            identity = record["reservation_id"]
            if record["state"] != "tentative" or identity in account["reservations"]:
                raise IntegrityError("New reservations must be unique tentative holds")
            if record["capacity_snapshot_id"] != account["snapshot"]["record_id"]:
                raise IntegrityError("Reservation refers to a stale capacity snapshot")
            if not any(record["resource_requests"].values()):
                raise IntegrityError("Reservation must request at least one resource")
            if any(item["case_id"] == record["case_id"] and _reservation_state(item,now) in {"tentative","committed"} for item in account["reservations"].values()):
                raise IntegrityError("Case already has an active resource reservation")
            account["reservations"][identity] = {"record_id":record["record_id"],"last_record_id":record["record_id"],"case_id":record["case_id"],"state":"tentative","expires_at":record["expires_at"],"remaining":deepcopy(record["resource_requests"]),"occupied_units":0}
        else:
            _action_shape(record)
            operation = record["operation"]
            quantities = record["quantities"]
            if operation == "cash":
                if record["reservation_id"] is not None or any(quantities.values()):
                    raise IntegrityError("Unallocated cash records must not carry reservation resource quantities")
                _cash(account,record)
            else:
                reservation = account["reservations"].get(record["reservation_id"])
                if reservation is None:
                    raise IntegrityError("Reservation action refers to a missing hold")
                state = _reservation_state(reservation,now)
                if operation == "commit":
                    if state != "tentative" or any(quantities.values()):
                        raise IntegrityError("Only a current tentative hold can be committed")
                    reservation["state"] = "committed"
                elif operation == "release":
                    if state not in {"tentative","committed","expired"} or reservation["occupied_units"] or any(quantities.values()):
                        raise IntegrityError("Cannot close occupied storage or an already closed reservation")
                    reservation["state"] = "closed" if state == "committed" else "released"
                    reservation["remaining"] = dict.fromkeys(RESOURCES,0)
                else:
                    if state != "committed":
                        raise IntegrityError("Only committed resources can be consumed or occupied/vacated")
                    if operation == "consume":
                        if quantities["storage_units"] or not (quantities["cash_cents"] or quantities["work_minutes"]):
                            raise IntegrityError("Consumption must name cash/time; storage uses occupancy records")
                        if quantities["cash_cents"]:
                            _cash(account,record,consumption=True)
                        period = record["work_period"]
                        if quantities["work_minutes"]:
                            if period not in account["work_periods"]:
                                raise IntegrityError("Work fact refers to an unrecorded budget period")
                            account["spent_work"][period] = account["spent_work"].get(period,0)+quantities["work_minutes"]
                        for resource in ("cash_cents","work_minutes"):
                            # Real overruns remain facts; they never manufacture a
                            # negative hold or restore previously consumed stock.
                            reservation["remaining"][resource] = max(0,reservation["remaining"][resource]-quantities[resource])
                    elif operation == "occupy":
                        if quantities["cash_cents"] or quantities["work_minutes"] or not quantities["storage_units"]:
                            raise IntegrityError("Occupancy must name only positive storage units")
                        reservation["occupied_units"] += quantities["storage_units"]
                    elif operation == "vacate":
                        amount = quantities["storage_units"]
                        if quantities["cash_cents"] or quantities["work_minutes"] or not amount or amount > reservation["occupied_units"]:
                            raise IntegrityError("Vacating must name actually occupied units")
                        reservation["occupied_units"] -= amount
                        reservation["remaining"]["storage_units"] = max(0,reservation["remaining"]["storage_units"]-amount)
                    else:
                        raise IntegrityError("Unsupported resource transition")
                reservation["last_record_id"] = record["record_id"]
        account["revision"] += 1
    return accounts


def projection_rows(events):
    return [(scope,int(synthetic),canonical_bytes(account),digest(account,domain="projection")) for (scope,synthetic),account in sorted(reduce_resources(events).items())]


def resource_view(account, *, as_of):
    """Numeric preview preserves unknowns and deficits; it does not qualify cash."""
    now = parse_timestamp(as_of)
    snapshot = account["snapshot"]
    held = dict.fromkeys(RESOURCES,0)
    reservations = {}
    for identity,item in account["reservations"].items():
        state = _reservation_state(item,now)
        reservations[identity] = dict(deepcopy(item),state=state)
        if state in {"tentative","committed"}:
            for resource in RESOURCES:
                held[resource] += max(item["remaining"][resource],item["occupied_units"]) if resource == "storage_units" else item["remaining"][resource]
    if snapshot is None:
        return {"revision":account["revision"],"snapshot_id":None,"available":dict.fromkeys(RESOURCES),"held":held,"reservations":reservations,"reason_codes":["capacity_snapshot_missing"],"purchase_authorized":False}
    reflected = set(snapshot["reflected_cash_ids"])
    delta = 0
    unknown = False
    reasons = []
    for identity,event in account["cash_events"].items():
        if event["state"] != "cleared":
            continue
        if identity in reflected:
            if parse_timestamp(event["occurred_at"]) > parse_timestamp(snapshot["as_of"]):
                reasons.append("cash_reflection_time_conflict")
            continue
        if parse_timestamp(event["occurred_at"]) <= parse_timestamp(snapshot["as_of"]):
            reasons.append("cash_before_watermark_unreconciled")
        if event["delta"] is None:
            unknown = True
        else:
            delta += event["delta"]
    cleared = snapshot["cleared_cash_cents"]+delta if snapshot["cleared_cash_cents"] is not None and not unknown else None
    burdens = [snapshot[key] for key in ("obligations_cents","cash_floor_cents","incremental_stress_cents","active_cash_holds_cents")]
    available_cash = cleared-sum(burdens)-held["cash_cents"] if cleared is not None and all(value is not None for value in burdens) and not reasons else None
    work = snapshot["work_minutes"]-account["spent_work"].get(snapshot["work_period"],0)-held["work_minutes"] if snapshot["work_minutes"] is not None else None
    storage = snapshot["storage_units"]-held["storage_units"] if snapshot["storage_units"] is not None else None
    available = {"cash_cents":available_cash,"work_minutes":work,"storage_units":storage}
    if any(value is None for value in available.values()):
        reasons.append("resource_amount_unresolved")
    if any(value is not None and value < 0 for value in available.values()):
        reasons.append("resource_deficit")
    if parse_timestamp(snapshot["as_of"]) > now or parse_timestamp(snapshot["created_at"]) > now or parse_timestamp(snapshot["valid_until"]) <= now:
        reasons.append("capacity_snapshot_not_current")
    return {"revision":account["revision"],"snapshot_id":snapshot["record_id"],"cleared_cash_preview_cents":cleared,"known_unreflected_cash_delta_cents":delta,"available":available,"held":held,"reservations":reservations,"reason_codes":reasons,"purchase_authorized":False}


def _resolver(events):
    return {record["record_id"]:record for _,record in events}.get


def _qualified_view(account,events,*,as_of,verify_claim,for_intake=True):
    original_verifier, verified = verify_claim, {}
    if original_verifier is not None:
        def verify_claim(identity,**kwargs):
            if identity not in verified:
                verified[identity] = read_verification(original_verifier(identity,**kwargs))
            return deepcopy(verified[identity])
    result = resource_view(account,as_of=as_of)
    snapshot = account["snapshot"]
    if snapshot is None:
        result["state"] = "unknown"
        return result
    resolve = _resolver(events)
    check = subject_claims(snapshot["evidence_claim_ids"],snapshot,as_of=as_of,resolve=resolve,verify_claim=verify_claim)
    required = {"scope","cleared_cash_cents","obligations_cents","cash_floor_cents","incremental_stress_cents","active_cash_holds_cents","work_minutes","work_period","storage_units","bank_watermark","reflected_cash_ids","holds_exclude_internal_reservations","policy_id","as_of","valid_until"}
    result["reason_codes"].extend(check["reason_codes"])
    if not required.issubset(check["verified_fields"]) or snapshot["bank_watermark"] is None:
        result["reason_codes"].append("capacity_basis_unverified")
    policy = resolve(snapshot["policy_id"])
    policy_check = {"input_digests":{},"verification_digests":{},"reason_codes":[]}
    if policy is None:
        result["reason_codes"].append("capacity_policy_missing")
    else:
        policy = read_record(policy,expected_type="operating_policy")
        checked = assess_operating_policy(policy,as_of=as_of,synthetic=snapshot["synthetic"],verify_claim=verify_claim)
        result["reason_codes"].extend(checked["reason_codes"])
        policy_check = subject_claims(policy["basis_claim_ids"],policy,as_of=as_of,resolve=resolve,verify_claim=verify_claim)
        result["reason_codes"].extend(policy_check["reason_codes"])
        if policy["scope"] != snapshot["scope"] or policy["cash_floor_cents"] != snapshot["cash_floor_cents"] or policy["incremental_stress_cents"] != snapshot["incremental_stress_cents"]:
            result["reason_codes"].append("capacity_policy_scope_or_floor_mismatch")
        result["policy_sha256"] = record_digest(policy)
    result["input_digests"] = dict(check["input_digests"],**{snapshot["record_id"]:record_digest(snapshot)})
    result["verification_digests"] = check["verification_digests"]
    result["input_digests"].update(policy_check["input_digests"])
    result["verification_digests"].update(policy_check["verification_digests"])
    # Retention/review can change after insertion. A previously admitted action
    # remains history, but cannot silently keep qualifying resources for intake.
    reflected = set(snapshot["reflected_cash_ids"])
    current_cash = {event["record_id"] for identity,event in account["cash_events"].items() if identity not in reflected and event["state"] == "cleared"}
    for _,record in events:
        if record["record_type"] != "resource_action" or record["scope"] != snapshot["scope"] or record["synthetic"] != snapshot["synthetic"]:
            continue
        if record["operation"] == "commit":
            # Keeping an existing hold committed cannot create available stock.
            # Manual preflight derives this transition from protected decisions.
            continue
        if record["operation"] == "cash" and record["record_id"] not in current_cash:
            continue
        action_check = subject_claims(record["basis_claim_ids"],record,as_of=as_of,resolve=resolve,verify_claim=verify_claim)
        result["input_digests"][record["record_id"]] = record_digest(record)
        result["input_digests"].update(action_check["input_digests"])
        result["verification_digests"].update(action_check["verification_digests"])
        if action_check["state"] != "pass" or not ACTION_FIELDS.issubset(action_check["verified_fields"]):
            result["reason_codes"].append("resource_action_basis_unverified:"+record["record_id"])
        result["reason_codes"].extend(action_check["reason_codes"])
    if for_intake:
        from .portfolio import intake_state
        control = intake_state(events,scope=snapshot["scope"],synthetic=snapshot["synthetic"],as_of=as_of)
        if control["decision_id"] is not None:
            result["input_digests"][control["decision_id"]] = control["decision_sha256"]
        if control["paused"]:
            result["reason_codes"].append("intake_paused")
    result["state"] = "fail" if "resource_deficit" in result["reason_codes"] or check["state"] == "fail" else "unknown" if result["reason_codes"] else "pass"
    return result


def status(path, *, scope, synthetic, verify_claim=None):
    with store._connection(path) as connection:
        events = store._events(connection)
        account = reduce_resources(events).get((scope,synthetic),_new_account())
        return _qualified_view(account,events,as_of=utc_now(),verify_claim=verify_claim)


def import_snapshot(path, snapshot, *, request_key):
    snapshot = read_record(snapshot,expected_type="capacity_snapshot")
    # Retain explicit facts even when amounts remain unknown. Reservations must
    # separately verify the current snapshot and scoped policy before using it.
    with store._connection(path,write=True) as connection:
        events = store._events(connection)
        if connection.execute("SELECT 1 FROM request_receipts WHERE request_key=?",(request_key,)).fetchone():
            return store._append(connection,snapshot,request_key=request_key)
        reduce_resources(events+[(len(events)+1,snapshot)])
        return store._append(connection,snapshot,request_key=request_key)


def reserve(path, reservation, *, request_key, verify_claim=None):
    reservation = read_record(reservation,expected_type="reservation")
    if reservation["idempotency_key"] != request_key:
        raise ConflictError("Reservation idempotency key differs from the request key")
    with store._connection(path,write=True) as connection:
        events = store._events(connection)
        if connection.execute("SELECT 1 FROM request_receipts WHERE request_key=?",(request_key,)).fetchone():
            return store._append(connection,reservation,request_key=request_key)
        now = utc_now()
        if parse_timestamp(reservation["created_at"]) > parse_timestamp(now) or reservation["expires_at"] is None or parse_timestamp(reservation["expires_at"]) <= parse_timestamp(now):
            raise UnresolvedError("Reservation is not current; expiry cannot be reactivated")
        account = reduce_resources(events).get((reservation["scope"],reservation["synthetic"]),_new_account())
        view = _qualified_view(account,events,as_of=now,verify_claim=verify_claim)
        if view["state"] != "pass":
            raise UnresolvedError("Capacity is not qualified: "+", ".join(view["reason_codes"]))
        policy = _resolver(events)(account["snapshot"]["policy_id"])
        expiry = parse_timestamp(reservation["expires_at"])
        if expiry > parse_timestamp(account["snapshot"]["valid_until"]) or (expiry-parse_timestamp(reservation["created_at"])) > timedelta(seconds=policy["validity_seconds"]):
            raise UnresolvedError("Reservation expiry exceeds its reviewed capacity or policy validity")
        if any(reservation["resource_requests"][key] > view["available"][key] for key in RESOURCES):
            raise UnresolvedError("Insufficient capacity; no resource was partially reserved")
        reduce_resources(events+[(len(events)+1,reservation)])
        finished = parse_timestamp(utc_now())
        if finished < parse_timestamp(now) or finished >= min(expiry,parse_timestamp(account["snapshot"]["valid_until"]),parse_timestamp(policy["valid_until"])):
            raise UnresolvedError("Capacity or request expired during reservation checks")
        return store._append(connection,reservation,request_key=request_key)


def record_action(path, action, *, request_key, verify_claim=None):
    action = read_record(action,expected_type="resource_action")
    with store._connection(path,write=True) as connection:
        events = store._events(connection)
        if connection.execute("SELECT 1 FROM request_receipts WHERE request_key=?",(request_key,)).fetchone():
            return store._append(connection,action,request_key=request_key)
        now = utc_now()
        if parse_timestamp(action["created_at"]) > parse_timestamp(now):
            raise UnresolvedError("Resource action cannot be from the future")
        account = reduce_resources(events).get((action["scope"],action["synthetic"]),_new_account())
        if action["operation"] == "release":
            from .decisions import require_reconciled_release
            require_reconciled_release(events,scope=action["scope"],synthetic=action["synthetic"],reservation_id=action["reservation_id"])
        if action["operation"] == "commit":
            target = account["reservations"].get(action["reservation_id"])
            if target is None or _reservation_state(target,parse_timestamp(now)) != "tentative":
                raise UnresolvedError("A current tentative reservation is required")
            if _qualified_view(account,events,as_of=now,verify_claim=verify_claim)["state"] != "pass":
                raise UnresolvedError("Commitment requires current qualified capacity")
        check = subject_claims(action["basis_claim_ids"],action,as_of=now,resolve=_resolver(events),verify_claim=verify_claim)
        if check["state"] != "pass" or not ACTION_FIELDS.issubset(check["verified_fields"]):
            raise UnresolvedError("Resource fact or closeout needs reviewed, bound evidence")
        reduce_resources(events+[(len(events)+1,action)])
        if action["operation"] == "commit":
            finished = parse_timestamp(utc_now())
            policy = _resolver(events)(account["snapshot"]["policy_id"])
            if finished < parse_timestamp(now) or finished >= min(parse_timestamp(target["expires_at"]),parse_timestamp(account["snapshot"]["valid_until"]),parse_timestamp(policy["valid_until"])):
                raise UnresolvedError("Tentative hold or qualified capacity expired during commitment checks")
        return store._append(connection,action,request_key=request_key)
