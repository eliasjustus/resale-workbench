"""Explicit private operations policy; never read or merge resale.toml."""
from pathlib import Path
import tomllib

from .clock import parse_timestamp
from .contracts import read_record
from .errors import ContractError
from .serialization import digest

_MATERIAL = ("cash_floor_cents", "incremental_stress_cents", "labor_cents_per_hour", "minimum_contribution_cents", "contribution_rule", "labor_allocation_rule", "research_allocation_rule", "deadline_policy", "clock_skew_seconds", "validity_seconds", "valid_until")


def read_policy(path):
    """None means absent. Missing explicitly named files are input errors.

    TOML cannot express null: omitted nullable policy fields become null, not
    example defaults. Required arrays must be supplied explicitly.
    """
    if path is None:
        return None
    try:
        document = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError("Cannot read selected operations policy") from exc
    if set(document) != {"schema_version", "policy"} or type(document["schema_version"]) is not int or document["schema_version"] != 1 or type(document["policy"]) is not dict:
        raise ContractError("Expected operations configuration version 1 and a policy table")
    policy = document["policy"]
    for field in _MATERIAL:
        policy.setdefault(field, None)
    return read_record(policy, expected_type="operating_policy")


def policy_snapshot(policy):
    if policy is None:
        return {"policy": None, "sha256": None}
    validated = read_record(policy, expected_type="operating_policy")
    return {"policy": validated, "sha256": digest(validated, domain="policy")}


def policy_prerequisites(policy, *, as_of, synthetic=False):
    """Presence/currentness checks only; authority must also be verified later."""
    now = parse_timestamp(as_of)
    if policy is None:
        return ["operating_policy_missing"]
    policy = read_record(policy, expected_type="operating_policy")
    reasons = [f"{field}_unresolved" for field in _MATERIAL if policy[field] is None]
    for field in ("authority_refs", "source_authority_refs", "cost_coverage"):
        if not policy[field]:
            reasons.append(f"{field}_unresolved")
    if policy["synthetic"] != synthetic:
        reasons.append("synthetic_boundary_mismatch")
    if parse_timestamp(policy["created_at"]) > now or parse_timestamp(policy["effective_at"]) > now:
        reasons.append("policy_not_yet_effective")
    if policy["valid_until"] and parse_timestamp(policy["valid_until"]) <= now:
        reasons.append("policy_expired")
    return reasons
