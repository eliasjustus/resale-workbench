"""Strict shape/clock readers. Successful parsing is not evidence verification."""
from copy import deepcopy
from importlib.resources import files

from jsonschema import Draft202012Validator, FormatChecker, validators

from .clock import parse_timestamp
from .errors import ContractError, IntegrityError, UnresolvedError
from .serialization import canonical_bytes, digest, load_json

_SCHEMA = load_json(files("operations").joinpath("resources/contracts.schema.json").read_text(encoding="utf-8"))
RECORD_TYPES = frozenset(_SCHEMA["$defs"])
_FORMATS = FormatChecker()


@_FORMATS.checks("date-time", raises=ContractError)
def _timestamp(value):
    if value is not None:
        parse_timestamp(value)
    return True


_Validator = validators.extend(Draft202012Validator, type_checker=Draft202012Validator.TYPE_CHECKER.redefine("integer", lambda checker, value: type(value) is int))
_VALIDATORS = {name: _Validator({"$ref": f"#/$defs/{name}", "$defs": _SCHEMA["$defs"]}, format_checker=_FORMATS) for name in RECORD_TYPES}

# Named foreign-record fields. Free-text labels are not silently treated as evidence.
REFERENCE_TYPES = {
    "producer_ref": {"actor_authority"}, "reviewer_ref": {"actor_authority"},
    "actor_ref": {"actor_authority"}, "authority_refs": {"actor_authority"},
    "authority_ids": {"actor_authority"}, "participant_authority_ids": {"actor_authority"},
    "source_rule_id": {"source_rule"}, "source_authority_refs": {"source_rule"},
    "rights_rule_ids": {"source_rule"}, "policy_id": {"operating_policy"},
    "capacity_snapshot_id": {"capacity_snapshot"}, "readiness_snapshot_id": {"readiness_snapshot"},
    "initial_judgment_ref": {"initial_judgment"}, "advice_exposure_log_ref": {"advice_exposure"},
    "decision_id": {"human_decision"}, "prior_attempt_id": {"execution_attempt"},
    "attempt_id": {"review_attempt"},
    "claim_id": {"material_claim"}, "competence_claim_ids": {"material_claim"},
    "conflicting_claim_ids": {"material_claim"}, "affected_claim_ids": {"material_claim"},
    "left_evidence_id": {"evidence_event"}, "right_evidence_id": {"evidence_event"},
    "supersedes_edge_id": {"lineage_edge"},
    "upstream_evaluation_id": {"upstream_evaluation"}, "cost_policy_id": {"cost_policy"},
    "request_id": {"readiness_request"}, "operating_review_id": {"operating_review"},
    "protocol_id": {"inspection_protocol"}, "inspector_ref": {"actor_authority"},
    "evidence_event_id": {"evidence_event"}, "adjustment_policy_id": {"comparison_adjustment"},
    "evidence_event_ids": {"evidence_event"}, "lineage_edge_ids": {"lineage_edge"},
    "route_id": {"transaction_route"}, "deadline_ids": {"reviewed_deadline"},
    "trigger_claim_id": {"material_claim"}, "binding_claim_ids": {"material_claim"},
    "cohort_id": {"cohort_definition"}, "supersedes_event_id": {"outcome_event"},
    "related_event_ids": {"evidence_event"}, "reflected_event_ids": {"outcome_event"},
    "material_claim_ids": {"material_claim"}, "basis_claim_ids": {"material_claim"},
    "eligibility_claim_ids": {"material_claim"}, "ownership_claim_ids": {"material_claim"},
    "evidence_claim_ids": {"material_claim"}, "capability_claim_ids": {"material_claim"},
    "grant_claim_ids": {"material_claim"}, "revocation_claim_ids": {"material_claim"},
    "reconciliation_claim_ids": {"material_claim"}, "reconciliation_ref": {"material_claim"},
    "terms_ref": {"material_claim"}, "return_terms_ref": {"material_claim"},
    "testing_scope_refs": {"material_claim"}, "reputation_context_ref": {"material_claim"},
    "inspection_before_release_plan_ref": {"inspection_protocol"},
    "inspection_case_id": {"inspection_case"}, "owner_actor_ref": {"actor_authority"},
    "adjudicator_ref": {"actor_authority"}, "label_id": {"benchmark_label"},
    "supersedes_label_id": {"benchmark_label"}, "exit_review_id": {"exit_review"},
    "offer_id": {"offer_item"}, "current_offer_id": {"offer_item"},
    "assignment_id": {"documentation_assignment"},
    "claim_deadline_review_ref": {"material_claim"}, "basis_refs": {"material_claim"},
    "input_record_ids": RECORD_TYPES,
}
REFERENCE_OVERRIDES = {("documentation_assignment","protocol_id"):{"documentation_protocol"}}


def schema_snapshot():
    return deepcopy(_SCHEMA)


def _semantics(record):
    created = parse_timestamp(record["created_at"])
    for field in ("observed_at", "event_at", "occurred_at", "recorded_at", "exposed_at", "frozen_at", "as_of", "checked_at", "reviewed_at", "deleted_at"):
        if record.get(field) is not None and parse_timestamp(record[field]) > created:
            raise ContractError("Evidence time cannot follow record creation", path=field)
    if record.get("event_at") and parse_timestamp(record["event_at"]) > parse_timestamp(record["observed_at"]):
        raise ContractError("Event cannot follow observation", path="event_at")
    if record.get("valid_until"):
        start = record.get("effective_at", record.get("as_of", record["created_at"]))
        if parse_timestamp(record["valid_until"]) <= parse_timestamp(start):
            raise ContractError("Validity must end after its start", path="valid_until")
    if record["record_type"] == "reservation" and record["state"] == "tentative":
        if record["expires_at"] is None or parse_timestamp(record["expires_at"]) <= created:
            raise ContractError("Tentative reservations require a future expiry", path="expires_at")
    if record["record_type"] == "actor_authority" and record["revoked_at"]:
        revoked = parse_timestamp(record["revoked_at"])
        if revoked < parse_timestamp(record["effective_at"]) or revoked > created:
            raise ContractError("Revocation must fall between authority start and recording")
        if not record["revocation_claim_ids"]:
            raise ContractError("Revocation requires evidence")
    if record["record_type"] == "evidence_event":
        if record["kind"] != "transaction" and record["transaction_state"] != "not_applicable":
            raise ContractError("Nontransaction evidence cannot assert transaction status")
    if record["record_type"] == "operating_review":
        exposures = [line["exposure_id"] for line in record["cost_lines"]]
        if len(exposures) != len(set(exposures)):
            raise ContractError("Cost exposure must occur only once", path="cost_lines")
        for line in record["cost_lines"]:
            if not record["synthetic"] and line["basis_kind"] == "synthetic":
                raise ContractError("Synthetic cost basis cannot support a real operating review")
            if line["incurred_at"] and parse_timestamp(line["incurred_at"]) > created:
                raise ContractError("Incurred cost cannot be in the future")
            if line["incurred_at"] and line["effective_until"] and parse_timestamp(line["effective_until"]) < parse_timestamp(line["incurred_at"]):
                raise ContractError("Cost period ends before it starts")
        if record["scenario_status"] == "complete" and (any(record[k] is None for k in ("item_receipt_cents", "shipping_collected_cents", "acquisition_cents")) or any(line["amount_cents"] is None or line["basis_kind"] == "unresolved" for line in record["cost_lines"])):
            raise ContractError("Complete scenario cannot contain unknown amounts or cost bases")
    if record["record_type"] == "readiness_snapshot":
        states = {check["state"] for check in record["checks"].values()}
        expected = "blocked" if "fail" in states else "unresolved" if "unknown" in states else "ready_for_human_review"
        if record["status"] != expected:
            raise ContractError("Readiness headline disagrees with its checks", path="status")


def read_record(value, *, expected_type=None):
    """Return an independent validated value; no I/O or authority assertion."""
    if isinstance(value, (str, bytes)):
        value = load_json(value)
    canonical_bytes(value)
    if type(value) is not dict or type(value.get("record_type")) is not str or value["record_type"] not in RECORD_TYPES:
        raise ContractError("Unknown operational record type")
    if expected_type is not None and value["record_type"] != expected_type:
        raise ContractError("Unexpected operational record type")
    error = next(_VALIDATORS[value["record_type"]].iter_errors(value), None)
    if error:
        raise ContractError(error.message, path=".".join(map(str, error.absolute_path)))
    _semantics(value)
    return deepcopy(value)


def record_digest(value):
    return digest(read_record(value))


def resolve_references(value, resolver, *, as_of):
    """Verify immediate typed references for a view, not transitive source truth.

    resolver(id) returns a stored record or None. A producer reference is an
    accountability link, never proof of an authenticated host session. Root
    authority bootstrapping and retained-file verification belong to their
    dedicated services; this reader cannot grant permission.
    """
    record = read_record(value)
    instant = parse_timestamp(as_of)
    result = {}

    def walk(item):
        if type(item) is list:
            for child in item:
                walk(child)
        elif type(item) is dict:
            for field, content in item.items():
                expected_types = REFERENCE_OVERRIDES.get((record["record_type"],field),REFERENCE_TYPES.get(field))
                if expected_types is not None:
                    for identity in (content if type(content) is list else [content]):
                        if identity is None:
                            continue
                        target = resolver(identity)
                        if target is None:
                            raise UnresolvedError("Referenced record is missing", path=field)
                        target = read_record(target)
                        if target["record_id"] != identity or target["record_type"] not in expected_types or target["synthetic"] != record["synthetic"]:
                            raise IntegrityError("Reference identity, type or synthetic boundary mismatch", path=field)
                        if parse_timestamp(target["created_at"]) > instant or (target.get("effective_at") and parse_timestamp(target["effective_at"]) > instant):
                            raise UnresolvedError("Reference is not available at this view time", path=field)
                        if target.get("valid_until") and parse_timestamp(target["valid_until"]) <= instant:
                            raise UnresolvedError("Reference has expired", path=field)
                        if target.get("revoked_at") and parse_timestamp(target["revoked_at"]) <= instant:
                            raise IntegrityError("Reference authority is revoked", path=field)
                        if target.get("status") == "contradicted":
                            raise IntegrityError("Reference is contradicted", path=field)
                        result[identity] = target
                else:
                    walk(content)
    walk(record)
    return result
