"""Frozen offline benchmark manifests, audited exclusions and explicit denominators."""
from .adjudication import assess_label
from .bindings import subject_claims
from .clock import parse_timestamp
from .contracts import read_record, record_digest
from .errors import IntegrityError
from .events import event_view
from .lineage import _Groups, group_events
from .serialization import digest
from .sources import SCOPE_FIELDS, assess_source
from .verification import read_verification


def _ratio(numerator,denominator):
    return {"numerator":numerator,"denominator":denominator} if denominator else None


def freeze(protocol,records,*,verify_claim=None,verify_edge=None):
    protocol = read_record(protocol,expected_type="benchmark_protocol")
    if parse_timestamp(protocol["as_of"]) > parse_timestamp(protocol["frozen_at"]):
        raise IntegrityError("Benchmark evidence cutoff follows its freeze")
    if protocol["synthetic"] != (protocol["sampling_kind"] == "synthetic_controls"):
        raise IntegrityError("Synthetic controls and real sampling kinds must remain separate")
    records = [read_record(record) for record in records]
    by_id = {record["record_id"]:record for record in records}
    if len(by_id) != len(records):
        raise IntegrityError("Benchmark record identities must be unique")
    identifiers = [case["case_id"] for case in protocol["cases"]]
    if len(identifiers) != len(set(identifiers)):
        raise IntegrityError("Benchmark case identities must be unique")
    visible = [record for record in records if record["synthetic"] == protocol["synthetic"] and parse_timestamp(record["created_at"]) <= parse_timestamp(protocol["frozen_at"])]
    resolve = {record["record_id"]:record for record in visible}.get
    events = [record for record in visible if record["record_type"] == "evidence_event"]
    edges = [record for record in visible if record["record_type"] == "lineage_edge"]
    edge_checks = {}
    def qualified_edge(edge,*,as_of):
        basis = subject_claims(edge["basis_claim_ids"],edge,as_of=as_of,resolve=resolve,verify_claim=verify_claim)
        review = read_verification(verify_edge(edge,as_of=as_of)) if verify_edge else {"state":"unknown"}
        edge_checks[edge["record_id"]] = {"record_sha256":record_digest(edge),"basis":basis,"review":review}
        return {"state":"pass" if basis["state"] == "pass" and set(edge).issubset(basis["verified_fields"]) and review["state"] == "pass" else "unknown"}
    input_view = event_view(events,as_of=protocol["as_of"],synthetic=protocol["synthetic"])
    input_ids = {record["record_id"] for record in input_view["records"]}
    grouping = group_events(input_view["records"],[edge for edge in edges if edge["left_evidence_id"] in input_ids and edge["right_evidence_id"] in input_ids],as_of=protocol["frozen_at"],synthetic=protocol["synthetic"],verify_edge=qualified_edge)
    cutoff_excluded = {item["record_id"] for item in input_view["excluded"]}
    visible_events = {record["record_id"]:record for record in events if record["record_id"] not in cutoff_excluded}
    groups = _Groups(visible_events)
    for group in grouping["partition_groups"]:
        for identity in group[1:]:
            groups.join(group[0],identity)
    # Even a disputed related-record link may leak case facts across a split.
    for event in visible_events.values():
        for related in event["related_event_ids"]:
            if related in visible_events:
                groups.join(event["record_id"],related)
    protocol_check = subject_claims(protocol["basis_claim_ids"],protocol,as_of=protocol["frozen_at"],resolve=resolve,verify_claim=verify_claim)
    protocol_qualified = protocol_check["state"] == "pass" and set(protocol).issubset(protocol_check["verified_fields"])
    partitions,cases,exposed_groups = {},{},set()
    for case in protocol["cases"]:
        reasons,rights = [],{}
        if not protocol_qualified:
            reasons.append("protocol_basis_unverified")
        if not case["evidence_refs"]:
            reasons.append("case_evidence_missing")
        if case["exposed_to_method"] is not False:
            reasons.append("method_exposure_present_or_unknown")
        case_groups = set()
        for reference in case["evidence_refs"]:
            identity = reference["record_id"]
            event = visible_events.get(identity)
            if event is None:
                reasons.append("case_evidence_missing_or_future:"+identity)
                continue
            if reference["record_sha256"] != record_digest(event):
                raise IntegrityError("Frozen case evidence digest changed")
            case_groups.add(groups.root(identity))
            if identity in grouping["unresolved_evidence_ids"]:
                reasons.append("case_lineage_unresolved:"+identity)
            rule = resolve(event["source_rule_id"])
            if rule is None:
                reasons.append("case_source_rule_missing:"+identity)
                continue
            rule = read_record(rule,expected_type="source_rule")
            matching_rules = [record for record in visible if record["record_type"] == "source_rule" and all(record[key] == rule[key] for key in SCOPE_FIELDS)]
            if matching_rules and matching_rules[-1]["record_id"] != rule["record_id"]:
                reasons.append("case_source_rule_displaced:"+identity)
                rule = matching_rules[-1]
            if rule["scope"] != protocol["scope"] or rule["purpose"] != protocol["source_purpose"]:
                reasons.append("case_source_scope_mismatch:"+identity)
            check = assess_source(rule,action="retention",context={key:rule[key] for key in SCOPE_FIELDS},as_of=protocol["frozen_at"],synthetic=protocol["synthetic"],resolve=resolve,verify_claim=verify_claim)
            rights[identity] = {"assessment":check,"rule_sha256":record_digest(rule),"basis":subject_claims(rule["basis_refs"],rule,as_of=protocol["frozen_at"],resolve=resolve,verify_claim=verify_claim)}
            if check["state"] != "pass":
                reasons.append("case_retention_rights_unverified:"+identity)
        for group in case_groups:
            partitions.setdefault(group,set()).add(case["partition"])
        if case["exposed_to_method"] is not False:
            exposed_groups.update(case_groups)
        label = assess_label(resolve(case["label_id"]) if case["label_id"] else None,case,protocol,visible,verify_claim=verify_claim)
        cases[case["case_id"]] = {"partition":case["partition"],"group_ids":sorted(case_groups),"exclusion_reasons":reasons,"rights":rights,"adjudication":label}
    for case in cases.values():
        if any(len(partitions[group]) > 1 for group in case["group_ids"]):
            case["exclusion_reasons"].append("leakage_group_crosses_partitions")
        if exposed_groups.intersection(case["group_ids"]):
            case["exclusion_reasons"].append("leakage_group_exposed_to_method")
    material = {"protocol_sha256":record_digest(protocol),"protocol_check":protocol_check,"grouping":grouping,"edge_checks":edge_checks,"related_partition_groups":groups.values(),"cases":cases}
    return dict(material,benchmark_version_sha256=digest(material,domain="projection"),purchase_authorized=False)


def score(protocol,frozen,records,predictions,*,verify_claim=None,verify_edge=None):
    current = freeze(protocol,records,verify_claim=verify_claim,verify_edge=verify_edge)
    if current != frozen:
        raise IntegrityError("Benchmark material changed; freeze a new version before comparing")
    selected_labels = {case["label_id"] for case in protocol["cases"] if case["label_id"] is not None}
    if any(record["record_type"] == "benchmark_label" and record["synthetic"] == protocol["synthetic"] and record["scope"] == protocol["scope"] and record["supersedes_label_id"] in selected_labels for record in records):
        raise IntegrityError("A selected label has a retained correction; withdraw this comparison and freeze a new version")
    selected_events = {item["record_id"] for case in protocol["cases"] for item in case["evidence_refs"]}
    relevant_events = set(selected_events)
    for group in current["related_partition_groups"]:
        if selected_events.intersection(group):
            relevant_events.update(group)
    if any(record["record_type"] == "lineage_edge" and record["synthetic"] == protocol["synthetic"] and parse_timestamp(record["created_at"]) > parse_timestamp(protocol["frozen_at"]) and relevant_events.intersection((record["left_evidence_id"],record["right_evidence_id"])) for record in records):
        raise IntegrityError("Later lineage evidence requires regrouping under a new benchmark version")
    if any(record["record_type"] == "source_rule" and record["synthetic"] == protocol["synthetic"] and record["scope"] == protocol["scope"] and record["purpose"] == protocol["source_purpose"] and parse_timestamp(record["created_at"]) > parse_timestamp(protocol["frozen_at"]) for record in records):
        raise IntegrityError("Later source authority requires review under a new benchmark version")
    by_id = {record["record_id"]:record for record in records}
    by_case = {}
    for value in predictions:
        prediction = read_record(value,expected_type="benchmark_prediction")
        if prediction["case_id"] in by_case or prediction["case_id"] not in current["cases"]:
            raise IntegrityError("Prediction must name one unique manifest case")
        if (prediction["benchmark_version_sha256"],prediction["method_identity_ref"],prediction["method_version"],prediction["synthetic"]) != (current["benchmark_version_sha256"],protocol["method_identity_ref"],protocol["method_version"],protocol["synthetic"]) or parse_timestamp(prediction["created_at"]) < parse_timestamp(protocol["frozen_at"]):
            raise IntegrityError("Prediction differs from its pre-frozen benchmark version")
        check = subject_claims(prediction["basis_claim_ids"],prediction,as_of=prediction["created_at"],resolve=by_id.get,verify_claim=verify_claim)
        by_case[prediction["case_id"]] = (prediction,check)
    audit = {}
    counts = dict(held_out_cases=0,excluded_cases=0,eligible_cases=0,missing_predictions=0,supported_predictions=0,missing_adjudications=0,adjudicated_supported_predictions=0,false_support=0)
    severity = {key:{"adjudicated_supported_predictions":0,"false_support":0} for key in ("minor","material","critical","unresolved")}
    for identity,case in current["cases"].items():
        entry = {"partition":case["partition"],"exclusion_reasons":list(case["exclusion_reasons"]),"adjudication":case["adjudication"],"prediction":None}
        audit[identity] = entry
        if case["partition"] != "held_out":
            continue
        counts["held_out_cases"] += 1
        if case["exclusion_reasons"]:
            counts["excluded_cases"] += 1
            continue
        counts["eligible_cases"] += 1
        label = case["adjudication"]
        if label["state"] != "pass":
            counts["missing_adjudications"] += 1
        pair = by_case.get(identity)
        if pair is None or pair[1]["state"] != "pass" or not set(pair[0]).issubset(pair[1]["verified_fields"]):
            counts["missing_predictions"] += 1
            continue
        prediction,check = pair
        entry.update(prediction=prediction["prediction"],prediction_check=check,prediction_sha256=record_digest(prediction))
        if prediction["prediction"] == "supported":
            counts["supported_predictions"] += 1
            if label["state"] == "pass":
                counts["adjudicated_supported_predictions"] += 1
                severity[label["severity"]]["adjudicated_supported_predictions"] += 1
                if label["label"] == "unsupported":
                    counts["false_support"] += 1
                    severity[label["severity"]]["false_support"] += 1
    result = {"benchmark_version_sha256":current["benchmark_version_sha256"],"sampling_kind":protocol["sampling_kind"],"synthetic":protocol["synthetic"],"counts":counts,"false_support_rate":_ratio(counts["false_support"],counts["adjudicated_supported_predictions"]),"support_coverage":_ratio(counts["supported_predictions"],counts["eligible_cases"]),"missing_adjudication_rate":_ratio(counts["missing_adjudications"],counts["eligible_cases"]),"severity":severity,"audit":audit,"natural_prevalence_estimate":None,"population_inference_status":"independent_sampling_frame_review_required" if protocol["sampling_kind"] == "prospective_consecutive" else "sampling_kind_does_not_estimate_natural_prevalence","purchase_authorized":False}
    return dict(result,report_sha256=digest(result,domain="projection"))
