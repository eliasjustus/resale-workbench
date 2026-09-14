"""Explicit statement and cash-fact entry points over the single resource ledger."""
from .capacity import import_snapshot, record_action
from .contracts import read_record
from .errors import ContractError


def import_statement(path, snapshot, *, request_key):
    """Statements declare stable reflected IDs, including not-yet-arrived debits."""
    return import_snapshot(path,snapshot,request_key=request_key)


def record_cash(path, event, *, request_key, verify_claim=None):
    """Retain qualified adverse facts even when the resulting balance is negative."""
    event = read_record(event,expected_type="resource_action")
    if event["operation"] != "cash":
        raise ContractError("Cash reconciliation requires an unallocated cash action")
    return record_action(path,event,request_key=request_key,verify_claim=verify_claim)
