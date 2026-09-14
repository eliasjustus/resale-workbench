"""Canonical operational JSON is distinct from the original evidence bytes."""
import hashlib
import json

from .errors import ContractError


def _json_value(value, path="$"):
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _json_value(item, f"{path}[{index}]")
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for key, item in value.items():
            _json_value(item, f"{path}.{key}")
        return
    raise ContractError("Only JSON objects, arrays, strings, booleans, integers and null are accepted", path=path)


def canonical_bytes(value):
    _json_value(value)
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (UnicodeError, ValueError) as exc:
        raise ContractError("Value cannot be represented as canonical UTF-8 JSON") from exc


def digest(value, *, domain="record"):
    if domain not in {"record", "policy", "request", "event", "projection", "schema"}:
        raise ContractError("Unknown digest domain")
    return hashlib.sha256(f"resale.operations/{domain}/v1\0".encode("ascii") + canonical_bytes(value)).hexdigest()


def load_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ContractError("Duplicate JSON key", path=key)
            result[key] = value
        return result

    def invalid_number(value):
        raise ContractError("Fractional or non-finite JSON numbers are not accepted")

    try:
        result = json.loads(text, object_pairs_hook=pairs, parse_float=invalid_number, parse_constant=invalid_number)
        canonical_bytes(result)
        return result
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError("Invalid JSON") from exc
