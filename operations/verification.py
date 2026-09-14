"""Typed boundary for optional qualified verification services."""
from .errors import ContractError


def read_verification(value):
    if type(value) is not dict or type(value.get("state")) is not str or value["state"] not in {"pass","fail","unknown"}:
        raise ContractError("Qualified verifier must return an object with state pass, fail or unknown")
    return value
