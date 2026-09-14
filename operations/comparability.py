"""Reviewed comparable transfer; source services never become target defaults."""
from .bindings import subject_claims
from .clock import parse_timestamp
from .contracts import read_record, record_digest
from .errors import IntegrityError
from .events import event_view, price_eligibility
from .evidence_adapter import PRICE_FIELDS
from .lineage import group_events
from .policies import assess_comparison_adjustment
from .serialization import digest
from .verification import read_verification


def _service_refs(bundle,*,as_of,synthetic,resolve,verify_claim):
    identities = [bundle["return_terms_ref"],*bundle["testing_scope_refs"],bundle["reputation_context_ref"]]
    reasons,inputs,proofs = [],{},{}
    for identity in dict.fromkeys(identities):
        if identity is None:
            reasons.append("service_reference_unresolved")
            continue
        claim = resolve(identity)
        if claim is None:
            reasons.append("service_reference_missing:"+identity)
            continue
        claim = read_record(claim,expected_type="material_claim")
        inputs[identity] = record_digest(claim)
        if claim["record_id"] != identity or claim["synthetic"] != synthetic:
            raise IntegrityError("Service reference identity or synthetic scope differs")
        if parse_timestamp(claim["created_at"]) > parse_timestamp(as_of) or claim["status"] == "contradicted":
            reasons.append("service_reference_not_current_or_contradicted:"+identity)
        checked = read_verification(verify_claim(identity,as_of=as_of,synthetic=synthetic)) if verify_claim else {"state":"unknown"}
        proofs[identity] = digest(checked,domain="projection")
        if checked["state"] != "pass" or checked.get("claim_id") != identity or checked.get("claim_sha256") != record_digest(claim) or checked.get("subject") != claim["subject"]:
            reasons.append("service_reference_unverified:"+identity)
    return {"state":"unknown" if reasons else "pass","reason_codes":reasons,"input_digests":inputs,"verification_digests":proofs}


def assess_comparisons(assessment,events,edges=(),*,as_of,resolve,verify_claim=None,verify_edge=None):
    events,edges = list(events),list(edges)
    assessment = read_record(assessment,expected_type="comparison_assessment")
    now = parse_timestamp(as_of)
    reasons = []
    if parse_timestamp(assessment["created_at"]) > now or parse_timestamp(assessment["valid_until"]) <= now:
        reasons.append("comparison_assessment_not_current")
    if assessment["scenario"] != "current_condition":
        reasons.append("conditional_offer_not_current_deliverability")
    target = assessment["target_offer"]
    for field,value in target.items():
        if value is None or field == "testing_scope_refs" and not value:
            reasons.append("target_offer_unknown:"+field)
    target_check = subject_claims(assessment["basis_claim_ids"],assessment,as_of=as_of,resolve=resolve,verify_claim=verify_claim)
    if target_check["state"] != "pass" or not {"case_id","scope","scenario","target_offer","comparisons","valid_until"}.issubset(target_check["verified_fields"]):
        reasons.append("target_deliverability_or_comparison_scope_unverified")
    target_refs = _service_refs(target,as_of=as_of,synthetic=assessment["synthetic"],resolve=resolve,verify_claim=verify_claim)
    reasons.extend(target_refs["reason_codes"])
    entries = {item["evidence_event_id"]:item for item in assessment["comparisons"]}
    if len(entries) != len(assessment["comparisons"]):
        raise IntegrityError("Each comparison event needs exactly one disposition")
    records = {record["record_id"]:read_record(record,expected_type="evidence_event") for record in events}
    if len(records) != len(events) or set(entries) != set(records):
        raise IntegrityError("Every supplied event must have an explicit comparison disposition")
    view = event_view(list(records.values()),as_of=as_of,synthetic=assessment["synthetic"])
    edge_proofs = {}
    def checked_edge(edge,**kwargs):
        result = read_verification(verify_edge(edge,**kwargs)) if verify_edge else {"state":"unknown"}
        edge_proofs[edge["record_id"]] = digest(result,domain="projection")
        return result
    grouping = group_events(list(records.values()),edges,as_of=as_of,synthetic=assessment["synthetic"],verify_edge=checked_edge)
    comparisons = []
    included = {}
    for identity,entry in entries.items():
        event = records[identity]
        codes = []
        transfer = subject_claims(entry["basis_claim_ids"],assessment,as_of=as_of,resolve=resolve,verify_claim=verify_claim)
        if transfer["state"] != "pass" or not {"target_offer","comparisons","scenario"}.issubset(transfer["verified_fields"]) or not entry["transfer_argument"]:
            codes.append("comparison_transfer_argument_unverified")
        if entry["disposition"] in {"disputed","unresolved"}:
            codes.append("comparison_"+entry["disposition"])
        adjustment = None
        event_check = None
        source_refs = None
        price = None
        if entry["disposition"] == "included":
            source_refs = _service_refs(event["service_bundle"],as_of=as_of,synthetic=assessment["synthetic"],resolve=resolve,verify_claim=verify_claim)
            codes.extend(source_refs["reason_codes"])
            price_check = price_eligibility(event,as_of=as_of,synthetic=assessment["synthetic"],reversal_ids=view["reversals"].get(identity,()))
            codes.extend(price_check["reason_codes"])
            event_check = subject_claims(event["material_claim_ids"],event,as_of=as_of,resolve=resolve,verify_claim=verify_claim)
            if event_check["state"] != "pass" or not (set(PRICE_FIELDS)|{"service_bundle"}).issubset(event_check["verified_fields"]):
                codes.append("source_price_or_service_bundle_unverified")
            if entry["observed_variant"] is None:
                codes.append("source_variant_unknown")
            if entry["observed_variant"] != target["variant"] and entry["adjustment_policy_id"] is None:
                codes.append("variant_transfer_requires_reviewed_adjustment")
            if target["condition"] != event["condition_basis"]:
                codes.append("condition_transfer_unresolved")
            if identity in grouping["unresolved_evidence_ids"]:
                codes.append("comparison_event_lineage_unresolved")
            price = event["item_price_cents"]
            if entry["adjustment_policy_id"] is not None:
                policy = resolve(entry["adjustment_policy_id"])
                if policy is not None and policy["record_id"] != entry["adjustment_policy_id"]:
                    raise IntegrityError("Comparison policy resolver returned another identity")
                adjustment = assess_comparison_adjustment(policy,assessment,evidence_event_id=identity,as_of=as_of,resolve=resolve,verify_claim=verify_claim)
                codes.extend(adjustment["reason_codes"])
                price = price+adjustment["amount_cents"] if price is not None and adjustment["amount_cents"] is not None else None
            if price is not None and price < 0:
                codes.append("adjusted_price_below_zero")
            if not codes:
                included[identity] = price
        comparisons.append({"evidence_event_id":identity,"disposition":entry["disposition"],"source_service_bundle":event["service_bundle"],"source_service_refs":source_refs,"transfer_argument":entry["transfer_argument"],"state":"unknown" if codes else "pass","reason_codes":codes,"adjusted_price_cents":price if not codes and entry["disposition"] == "included" else None,"transfer_check":transfer,"event_check":event_check,"adjustment":adjustment})
        reasons.extend(code+":"+identity for code in codes)
    material_events = []
    for group in grouping["event_groups"]:
        identities = [identity for identity in group if identity in included]
        if not identities:
            continue
        values = {included[identity] for identity in identities}
        if len(values) != 1:
            reasons.append("same_event_price_or_adjustment_conflict:"+",".join(group))
            continue
        material_events.append({"group_id":digest(sorted(group),domain="projection"),"evidence_event_ids":sorted(identities),"price_cents":values.pop()})
    if not material_events:
        reasons.append("no_material_comparison_events")
    result = {"state":"unknown" if reasons else "pass","reason_codes":list(dict.fromkeys(reasons)),"comparison_sha256":record_digest(assessment),"target_offer":target,"target_check":target_check,"scenario":assessment["scenario"],"comparisons":comparisons,"grouping":grouping,"material_events":material_events,"independent_event_count":len(material_events),"conditional_floor_cents":min((item["price_cents"] for item in material_events),default=None) if not reasons else None,"input_digests":{identity:record_digest(event) for identity,event in records.items()},"purchase_authorized":False}
    result["input_digests"].update({edge["record_id"]:record_digest(edge) for edge in edges})
    result["target_service_refs"] = target_refs
    result["input_digests"].update(target_refs["input_digests"])
    for entry in comparisons:
        if entry["source_service_refs"] is not None:
            result["input_digests"].update(entry["source_service_refs"]["input_digests"])
    result["edge_verification_digests"] = edge_proofs
    return dict(result,assessment_sha256=digest(result,domain="projection"))
