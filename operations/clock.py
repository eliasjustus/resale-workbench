"""Explicit timezone-bearing RFC3339 clocks; no implicit local timezone."""
from datetime import datetime, timezone
import re

from .errors import ContractError

_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$")


def parse_timestamp(value):
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value) or value.endswith("-00:00"):
        raise ContractError("Expected a timestamp with a known UTC offset")
    try:
        # datetime accepts offsets with minutes beyond 59; RFC3339 does not.
        if value[-1] != "Z" and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
            raise ValueError("invalid offset")
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ContractError("Invalid calendar timestamp") from exc


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
