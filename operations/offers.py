"""Reviewable truthful Markdown drafts, with no publication or contact surface."""
import html
import re

from .bindings import subject_claims
from .clock import parse_timestamp
from .contracts import read_record, record_digest
from .errors import IntegrityError, UnresolvedError
from .inspection import assess_inspection
from .serialization import digest


def _text(value):
    value = html.escape(str(value),quote=True).replace("\r"," ").replace("\n"," ")
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])",r"\\\1",value)


def assess(item,*,as_of,resolve,verify_claim=None):
    item = read_record(item,expected_type="offer_item")
    if parse_timestamp(item["created_at"]) > parse_timestamp(as_of) or parse_timestamp(item["valid_until"]) <= parse_timestamp(as_of) or item["price_cents"] is None:
        raise UnresolvedError("Offer price and current item review are required")
    basis = subject_claims(item["basis_claim_ids"],item,as_of=as_of,resolve=resolve,verify_claim=verify_claim)
    ownership = subject_claims(item["ownership_claim_ids"],item,as_of=as_of,resolve=resolve,verify_claim=verify_claim,claim_type="ownership")
    if basis["state"] != "pass" or not set(item).issubset(basis["verified_fields"]) or ownership["state"] != "pass" or not {"case_id","physical_item_id","scope","owner_actor_ref"}.issubset(ownership["verified_fields"]):
        raise UnresolvedError("Truthful offer facts, disclosures and actual ownership need qualified review")
    case = resolve(item["inspection_case_id"])
    if case is None:
        raise UnresolvedError("Retained inspection is required, including unavailable checks")
    case = read_record(case,expected_type="inspection_case")
    if (case["case_id"],case["scope"],case["synthetic"]) != (item["case_id"],item["scope"],item["synthetic"]) or case["scenario"] != "current_condition":
        raise IntegrityError("Offer inspection differs from the current item and scope")
    protocol = resolve(case["protocol_id"])
    if protocol is None:
        raise UnresolvedError("Inspection protocol is missing")
    inspection = assess_inspection(case,protocol,as_of=as_of,resolve=resolve,verify_claim=verify_claim)
    case_basis = inspection["checks"]["case_basis"]
    if case_basis["state"] != "pass" or not {"observed_configuration","case_id","scope"}.issubset(case_basis["verified_fields"]):
        raise UnresolvedError("Observed configuration is not qualified for a factual draft")
    rows = [("Item",item["title"]),("Price",str(item["price_cents"]//100)+"."+str(item["price_cents"]%100).zfill(2)+" EUR"),("Channel",item["channel"])]
    for name,value in case["observed_configuration"].items():
        rows.append((name,", ".join(value) if isinstance(value,list) else value if value is not None else "Unresolved"))
    coverage = {}
    for observation in case["check_results"]:
        proof = inspection["checks"].get("function:"+observation["check_id"],{})
        qualified = inspection["observation_prerequisites_qualified"] and observation["check_id"] in {check["check_id"] for check in protocol["checks"]} and observation["origin"] == "observed" and proof.get("state") == "pass" and "check_results" in proof.get("verified_fields",[])
        coverage[observation["check_id"]] = "completed" if qualified and observation["status"] in {"pass","fail"} else "unavailable_or_unverified"
        rows.append(("Check "+observation["check_id"],observation["status"] if qualified else "Unverified or unavailable"))
        # Known/reported defects survive either documentation arm, including
        # failures outside the standard protocol and unverified reports.
        if observation["status"] == "fail":
            rows.append(("Reported defect "+observation["check_id"],observation["notes"]))
    rows.append(("Working-item conclusion","Established by reviewed current inspection" if inspection["working_item_conclusion"] else "Not established"))
    rows.extend(("Defect",value) for value in item["defects"])
    rows.extend(("Mandatory disclosure",value) for value in item["mandatory_disclosures"])
    rows.extend(("Service",value) for value in item["services"])
    service = {"price_cents":item["price_cents"],"channel":item["channel"],"services":item["services"],"mandatory_disclosures":item["mandatory_disclosures"],"inspection_protocol_sha256":record_digest(protocol),"testing_coverage":coverage,"disclosure_policy":"all_reviewed_disclosures_and_all_reported_inspection_failures"}
    result = {"item_sha256":record_digest(item),"basis":basis,"ownership":ownership,"inspection":inspection,"rows":rows,"service_signature_sha256":digest(service,domain="projection"),"publication_authorized":False}
    # Rows are JSON arrays at the canonical boundary.
    result["rows"] = [list(row) for row in rows]
    return dict(result,material_sha256=digest(result,domain="projection"))


def render(item,*,arm,as_of,resolve,verify_claim=None):
    if arm not in {"plain","structured"}:
        raise IntegrityError("Unknown documentation arm")
    result = assess(item,as_of=as_of,resolve=resolve,verify_claim=verify_claim)
    lines = [_text(name)+": "+_text(value) for name,value in result["rows"]]
    body = "\n\n".join(lines) if arm == "plain" else "\n".join("- "+line for line in lines)
    return "# "+("SYNTHETIC " if item["synthetic"] else "")+"PRIVATE REVIEW DRAFT\n\n"+body+"\n\nPublication requires separate human review and authorization.\n"
