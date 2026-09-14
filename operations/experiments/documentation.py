"""Frozen physical-item assignments and intention-to-treat private reports."""
from .. import cohorts, decisions, offers, store
from ..bindings import subject_claims
from ..clock import parse_timestamp, utc_now
from ..contracts import read_record, record_digest
from ..errors import ConflictError, IntegrityError, OperationsError, UnresolvedError
from ..serialization import digest


def _protocol(protocol,records,*,as_of,verify_claim):
    protocol = read_record(protocol,expected_type="documentation_protocol")
    if parse_timestamp(protocol["created_at"]) > parse_timestamp(as_of) or parse_timestamp(protocol["frozen_at"]) > parse_timestamp(as_of) or parse_timestamp(protocol["valid_until"]) <= parse_timestamp(as_of):
        raise UnresolvedError("Documentation protocol is not current")
    ids = [item["physical_item_id"] for item in protocol["items"]]
    if not ids or len(ids) != len(set(ids)):
        raise IntegrityError("Each physical item must appear once in the frozen assignment frame")
    check = subject_claims(protocol["basis_claim_ids"],protocol,as_of=as_of,resolve=records.get,verify_claim=verify_claim)
    if check["state"] != "pass" or not set(protocol).issubset(check["verified_fields"]):
        raise UnresolvedError("Documentation protocol needs a reviewed frozen assignment frame")
    blocks,assessments,case_ids = {},{},set()
    for item in protocol["items"]:
        offer = records.get(item["offer_id"])
        if offer is None:
            raise UnresolvedError("Frozen offer is missing")
        offer = read_record(offer,expected_type="offer_item")
        if (record_digest(offer),offer["physical_item_id"],offer["scope"],offer["synthetic"],offer["channel"]) != (item["offer_sha256"],item["physical_item_id"],protocol["scope"],protocol["synthetic"],protocol["channel"]) or parse_timestamp(offer["created_at"]) > parse_timestamp(protocol["frozen_at"]):
            raise IntegrityError("Offer differs from its pre-frozen experimental item")
        assessment = offers.assess(offer,as_of=as_of,resolve=records.get,verify_claim=verify_claim)
        if offer["case_id"] in case_ids:
            raise IntegrityError("One outcome case cannot supply multiple independent items")
        case_ids.add(offer["case_id"])
        assessments[item["physical_item_id"]] = assessment
        blocks.setdefault(item["block_id"],[]).append(item["physical_item_id"])
    arms = {}
    for block,items in blocks.items():
        if len(items) < 2 or len({assessments[identity]["service_signature_sha256"] for identity in items}) != 1:
            raise UnresolvedError("Matched documentation blocks must hold price and substantive service constant")
        ordered = sorted(items,key=lambda identity:digest([protocol["seed_sha256"],block,identity],domain="projection"))
        offset = int(digest([protocol["seed_sha256"],block],domain="projection")[-1],16)%2
        arms.update({identity:("plain","structured")[(index+offset)%2] for index,identity in enumerate(ordered)})
    return protocol,assessments,arms


def assign(path,protocol_id,physical_item_id,*,record_id,request_key,verify_claim=None):
    intent = {"protocol_id":protocol_id,"physical_item_id":physical_item_id,"record_id":record_id}
    with store._connection(path,write=True) as connection:
        retry = decisions._retry(connection,request_key,intent,record_id,"documentation_assignment")
        if retry is not None:
            return retry
        events = store._events(connection)
        records = {record["record_id"]:record for _,record in events}
        if protocol_id not in records:
            raise UnresolvedError("Documentation protocol is missing")
        now = utc_now()
        protocol,assessments,arms = _protocol(records[protocol_id],records,as_of=now,verify_claim=verify_claim)
        if physical_item_id not in arms:
            raise IntegrityError("Item is outside the frozen assignment frame")
        if any(record["record_type"] == "documentation_assignment" and record["physical_item_id"] == physical_item_id and record["synthetic"] == protocol["synthetic"] for record in records.values()):
            raise ConflictError("A physical item already has an assignment; duplicate listings are not independent outcomes")
        item = next(item for item in protocol["items"] if item["physical_item_id"] == physical_item_id)
        offer = records[item["offer_id"]]
        if any(record["record_type"] == "documentation_assignment" and record["case_id"] == offer["case_id"] and record["synthetic"] == protocol["synthetic"] for record in records.values()):
            raise ConflictError("An outcome case already has a documentation assignment")
        if not any(record["record_type"] == "cohort_inclusion" and (record["cohort_id"],record["case_id"],record["scope"],record["synthetic"]) == (protocol["cohort_id"],offer["case_id"],protocol["scope"],protocol["synthetic"]) for record in records.values()):
            raise UnresolvedError("Every assigned item must first enter the outcome cohort")
        finished = utc_now()
        if parse_timestamp(finished) < parse_timestamp(now) or any(parse_timestamp(finished) >= parse_timestamp(records[item["offer_id"]]["valid_until"]) for item in protocol["items"]) or parse_timestamp(finished) >= parse_timestamp(protocol["valid_until"]):
            raise UnresolvedError("Documentation assignment expired during checks")
        record = {"record_type":"documentation_assignment","schema_version":1,"record_id":record_id,"created_at":finished,"synthetic":protocol["synthetic"],"producer_ref":protocol["producer_ref"],"protocol_id":protocol_id,"protocol_sha256":record_digest(protocol),"physical_item_id":physical_item_id,"offer_id":item["offer_id"],"case_id":offer["case_id"],"cohort_id":protocol["cohort_id"],"scope":protocol["scope"],"block_id":item["block_id"],"arm":arms[physical_item_id],"offer_material_sha256":assessments[physical_item_id]["material_sha256"],"request_sha256":digest(intent,domain="request")}
        return store._append(connection,read_record(record),request_key=request_key)


def report(path,protocol_id,*,as_of,verify_claim=None):
    with store._connection(path) as connection:
        records = {record["record_id"]:record for _,record in store._events(connection) if parse_timestamp(record["created_at"]) <= parse_timestamp(as_of)}
        protocol = read_record(records.get(protocol_id),expected_type="documentation_protocol")
        assignments = [record for record in records.values() if record["record_type"] == "documentation_assignment" and record["protocol_id"] == protocol_id]
        outcomes = cohorts.report(path,protocol["cohort_id"],as_of=as_of,verify_claim=verify_claim)
        arms = {arm:{"assigned_items":0,"known_inquiries":0,"known_operator_minutes":0,"known_views":0,"known_support_events":0,"unknown_observations":0,"cases":{}} for arm in ("plain","structured")}
        event_ids = {}
        observations = [record for record in records.values() if record["record_type"] == "documentation_observation" and parse_timestamp(record["occurred_at"]) <= parse_timestamp(as_of)]
        for observation in observations:
            event_ids.setdefault((observation["synthetic"],observation["event_id"]),[]).append(observation["record_id"])
        for assignment in assignments:
            arm = arms[assignment["arm"]]
            arm["assigned_items"] += 1
            original = records[assignment["offer_id"]]
            later = [record for record in records.values() if record["record_type"] == "offer_item" and record["physical_item_id"] == assignment["physical_item_id"] and record["synthetic"] == assignment["synthetic"]]
            latest = later[-1]
            changes = [field for field in ("price_cents","channel","services","inspection_case_id","defects","mandatory_disclosures") if latest[field] != original[field]]
            case = {"assignment_id":assignment["record_id"],"physical_item_id":assignment["physical_item_id"],"substantive_changes":changes,"deviations":[],"outcome":outcomes["cases"].get(assignment["case_id"]),"observation_checks":{}}
            try:
                current = offers.assess(latest,as_of=as_of,resolve=records.get,verify_claim=verify_claim)
                case["current_offer_material_sha256"] = current["material_sha256"]
                case["original_material_still_qualified"] = current["material_sha256"] == assignment["offer_material_sha256"]
            except OperationsError:
                case["original_material_still_qualified"] = False
            observed = [record for record in observations if record["assignment_id"] == assignment["record_id"]]
            if not observed:
                arm["unknown_observations"] += 1
            for observation in observed:
                check = subject_claims(observation["basis_claim_ids"],observation,as_of=as_of,resolve=records.get,verify_claim=verify_claim)
                case["observation_checks"][observation["record_id"]] = check
                current_offer = records.get(observation["current_offer_id"])
                qualified = check["state"] == "pass" and set(observation).issubset(check["verified_fields"]) and observation["synthetic"] == assignment["synthetic"] and len(event_ids[(observation["synthetic"],observation["event_id"])]) == 1 and parse_timestamp(observation["occurred_at"]) >= parse_timestamp(assignment["created_at"]) and current_offer is not None and current_offer["record_type"] == "offer_item" and (current_offer["physical_item_id"],current_offer["synthetic"],current_offer["case_id"],current_offer["scope"]) == (assignment["physical_item_id"],assignment["synthetic"],assignment["case_id"],assignment["scope"]) and parse_timestamp(current_offer["created_at"]) <= parse_timestamp(observation["occurred_at"])
                if observation["deviation_reason"]:
                    case["deviations"].append(observation["deviation_reason"])
                if not qualified or observation["count"] is None or observation["operator_minutes"] is None:
                    arm["unknown_observations"] += 1
                    continue
                arm["known_operator_minutes"] += observation["operator_minutes"]
                metric = {"inquiry":"known_inquiries","views":"known_views","support":"known_support_events"}.get(observation["event_type"])
                if metric:
                    arm[metric] += observation["count"]
            case["documentation_only_contrast_valid"] = not changes and not case["deviations"] and case["original_material_still_qualified"]
            case["change_disclosure_status"] = "recorded_deviation" if changes and case["deviations"] else "undocumented_change" if changes else "no_observed_substantive_change"
            arm["cases"][assignment["case_id"]] = case
        contributions = [(case["outcome"] or {}).get("realized_profit_cents") for arm in arms.values() for case in arm["cases"].values()]
        contribution = sum(contributions) if contributions and all(value is not None for value in contributions) else None
        return {"protocol_id":protocol_id,"protocol_sha256":record_digest(protocol),"as_of":as_of,"arms":arms,"assignment_frame_items":len(protocol["items"]),"unassigned_frame_items":len(protocol["items"])-len(assignments),"primary_endpoint_status":"qualified_assigned_case_contribution" if contribution is not None else "requires_reconciled_outcomes_and_complete_followup","views_are_primary_endpoint":False,"reconciled_contribution_cents":contribution,"causal_advantage_established":False,"published":False,"purchase_authorized":False}
