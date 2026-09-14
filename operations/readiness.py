"""Six independent checks. All pass permits human review, never a purchase."""
from copy import deepcopy

from .errors import ContractError
from .serialization import digest
from .verification import read_verification

CHECK_NAMES = ("rights","integrity","current_economics","operating_scenario","route","resources")


def compose(checks):
    """Preserve simultaneous failure and uncertainty instead of a combined score."""
    if type(checks) is not dict or set(checks) != set(CHECK_NAMES):
        raise ContractError("Readiness requires exactly the six independent checks")
    normalized = {}
    for name in CHECK_NAMES:
        check = read_verification(checks[name])
        codes = check.get("reason_codes",[])
        if type(codes) is not list or any(type(code) is not str or not code for code in codes):
            raise ContractError("Check reasons must be nonempty strings")
        normalized[name] = {"state":check["state"],"reasons":[{"code":code,"field":name,"evidence_refs":[]} for code in dict.fromkeys(codes)],"input_digests":[digest(check,domain="projection")]}
    states = {check["state"] for check in normalized.values()}
    status = "blocked" if "fail" in states else "unresolved" if "unknown" in states else "ready_for_human_review"
    return {"status":status,"checks":deepcopy(normalized),"purchase_authorized":False}
