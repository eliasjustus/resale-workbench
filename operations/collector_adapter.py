"""Opt-in action boundary; default missing material verifier stops dispatch."""
from .errors import UnresolvedError
from .sources import assess_source


def dispatch(perform, *, rule, action, context, as_of, synthetic, resolve, verify_claim=None):
    """Recheck on every invocation; never reuse an earlier permission result.

    perform is a host-owned callable. This module does not discover credentials,
    contact sellers, start schedules or select a live collector automatically.
    """
    assessment = assess_source(rule, action=action, context=context, as_of=as_of, synthetic=synthetic, resolve=resolve, verify_claim=verify_claim)
    if assessment["state"] != "pass":
        raise UnresolvedError("Source action blocked: " + ", ".join(assessment["reason_codes"]))
    return perform()
