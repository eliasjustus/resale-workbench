"""Mechanical integrity checks for direct browser captures and sold-search packets.

These checks cannot establish source authenticity, correct interpretation, or that
a displayed price was paid. No existing evaluation schemas are changed.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path, PureWindowsPath
import sys

CAPTURE_METHOD = "cua.direct_dom_snapshot_and_rendered_text.v1"
ROW_FIELDS = ("sale_id", "original_url", "title", "price_text", "shipping_text",
              "units_text", "sold_date_text", "country_text", "condition_text")
RESULT_KINDS = {"sold", "active_fallback", "empty", "blocked", "unknown"}
CHECK_SCOPE = "Mechanical integrity only; not authenticity or semantic proof."


class ValidationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValidationError(f"Cannot read JSON {path}: {exc}") from exc
    require(isinstance(value, dict), f"{path}: expected an object")
    return value


def relative_path(root, value):
    require(isinstance(value, str) and bool(value.strip()), "Path must be a nonempty relative string")
    path = Path(value)
    windows = PureWindowsPath(value)
    require(not path.is_absolute() and not windows.is_absolute() and not windows.drive
            and not windows.root, f"Absolute path forbidden: {value}")
    root = Path(root).resolve()
    resolved = (root / path).resolve()
    require(resolved.is_relative_to(root), f"Path escapes root: {value}")
    return resolved


def nonempty(value, label):
    require(isinstance(value, str) and bool(value.strip()), f"{label}: expected a nonempty string")


def raw_text(value, label):
    require(value is None or (isinstance(value, str) and bool(value.strip())),
            f"{label}: use a nonempty string or null for unknown")


def timestamp(value, label):
    nonempty(value, label)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{label}: invalid timestamp") from exc
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None,
            f"{label}: timestamp must include timezone")
    return parsed


def validate_capture(path):
    path = Path(path).resolve()
    if path.is_dir():
        path = relative_path(path, "capture.json")
    capture = read_json(path)
    require(type(capture.get("schema_version")) is int and capture["schema_version"] == 1,
            "Capture schema_version must be 1")
    require(capture.get("capture_method") == CAPTURE_METHOD, "Unsupported capture_method")
    nonempty(capture.get("source_url"), "source_url")
    require(capture.get("source_url_after") == capture["source_url"], "Source URL changed during capture")
    require(isinstance(capture.get("source_title"), str), "source_title must be a string")
    require(isinstance(capture.get("context"), dict), "context must be an object")
    started = timestamp(capture.get("started_at"), "started_at")
    completed = timestamp(capture.get("completed_at"), "completed_at")
    files = capture.get("files")
    require(isinstance(files, dict), "files must be an object")
    times, paths = [], []
    for kind in ("dom_snapshot", "rendered_text"):
        item = files.get(kind)
        require(isinstance(item, dict), f"Missing file descriptor: {kind}")
        raw_path = relative_path(path.parent, item.get("path"))
        require(raw_path.is_file(), f"Missing raw file: {kind}")
        paths.append(raw_path)
        try:
            data = raw_path.read_bytes()
            content = data.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise ValidationError(f"Cannot read UTF-8 raw file: {kind}") from exc
        require(bool(content.strip()), f"Empty raw file: {kind}")
        require(type(item.get("bytes")) is int and item["bytes"] == len(data), f"Byte count mismatch: {kind}")
        require(item.get("sha256") == hashlib.sha256(data).hexdigest(), f"SHA-256 mismatch: {kind}")
        times.append(timestamp(item.get("captured_at"), f"{kind}.captured_at"))
    require(paths[0] != paths[1] and path not in paths, "Raw files must be distinct from each other and the manifest")
    require(started <= times[0] <= times[1] <= completed, "Invalid capture chronology")
    return capture


def string_list(value, label):
    require(isinstance(value, list), f"{label} must be an array")
    for item in value:
        nonempty(item, label)


def validate_packet(path):
    path = Path(path).resolve()
    packet = read_json(path)
    require(type(packet.get("schema_version")) is int and packet["schema_version"] == 1,
            "Packet schema_version must be 1")
    require(packet.get("role") == "sold_search", "Packet role must be sold_search")
    nonempty(packet.get("case_id"), "case_id")
    attempts = packet.get("attempts")
    require(isinstance(attempts, list) and len(attempts) > 0, "attempts must be a nonempty array")
    for index, attempt in enumerate(attempts):
        label = f"attempts[{index}]"
        require(isinstance(attempt, dict), f"{label} must be an object")
        nonempty(attempt.get("query"), f"{label}.query")
        for field in ("displayed_period", "displayed_filters"):
            require(field in attempt, f"{label}.{field} is required")
            raw_text(attempt[field], f"{label}.{field}")
        kind = attempt.get("result_kind")
        require(isinstance(kind, str) and kind in RESULT_KINDS, f"{label}: invalid result_kind")
        capture_path = relative_path(path.parent, attempt.get("capture_path"))
        validate_capture(capture_path)
        rows = attempt.get("rows")
        require(isinstance(rows, list), f"{label}.rows must be an array")
        require(kind != "empty" or not rows, f"{label}: empty attempts cannot contain rows")
        for row in rows:
            require(isinstance(row, dict), f"{label}: rows must be objects")
            for field in ROW_FIELDS:
                require(field in row, f"{label}: row missing {field}")
                raw_text(row[field], f"{label}.{field}")
        string_list(attempt.get("limits"), f"{label}.limits")
    if "lessons" in packet:
        string_list(packet["lessons"], "lessons")
    return packet


def merge_packets(paths):
    """Group exact, known sale IDs within one case; retain every observation."""
    sold, unmatched, lessons, excluded = {}, [], [], []
    case_id = None
    for packet_path in paths:
        packet_path = Path(packet_path).resolve()
        packet = validate_packet(packet_path)
        if case_id is None:
            case_id = packet["case_id"]
        require(packet["case_id"] == case_id, "Cannot merge different case IDs")
        lessons.extend({"packet_path": str(packet_path), "text": value} for value in packet.get("lessons", []))
        for attempt_index, attempt in enumerate(packet["attempts"]):
            provenance = {"packet_path": str(packet_path), "attempt_index": attempt_index,
                          "capture_path": str(relative_path(packet_path.parent, attempt["capture_path"])),
                          "query": attempt["query"], "result_kind": attempt["result_kind"],
                          "displayed_period": attempt["displayed_period"],
                          "displayed_filters": attempt["displayed_filters"], "limits": attempt["limits"]}
            if attempt["result_kind"] != "sold":
                excluded.append({**provenance, "rows": attempt["rows"]})
                continue
            for row_index, row in enumerate(attempt["rows"]):
                observation = {**provenance, "row_index": row_index, "row": row}
                if row["sale_id"] is None:
                    unmatched.append(observation)
                else:
                    sold.setdefault(row["sale_id"], []).append(observation)
    require(case_id is not None, "At least one packet is required")
    evidence = []
    for sale_id, observations in sold.items():
        conflicts = {}
        for field in ROW_FIELDS:
            values = list(dict.fromkeys(o["row"][field] for o in observations if o["row"][field] is not None))
            if len(values) > 1:
                conflicts[field] = values
        evidence.append({"sale_id": sale_id, "observations": observations, "conflicts": conflicts})
    return {"schema_version": 1, "role": "sold_search_merge", "case_id": case_id,
            "check_scope": CHECK_SCOPE, "sold_evidence": evidence,
            "unmatched_sold_rows": unmatched, "excluded_attempts": excluded, "lessons": lessons}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate-capture", "validate-packet"):
        commands.add_parser(name).add_argument("path", type=Path)
    merge = commands.add_parser("merge")
    merge.add_argument("paths", nargs="+", type=Path)
    merge.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "merge":
            result = merge_packets(args.paths)
            with args.output.open("x", encoding="utf-8") as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
        elif args.command == "validate-capture":
            validate_capture(args.path)
        else:
            validate_packet(args.path)
    except (ValidationError, OSError) as exc:
        print(json.dumps({"valid": False, "error": str(exc), "check_scope": CHECK_SCOPE}))
        return 1
    print(json.dumps({"valid": True, "check_scope": CHECK_SCOPE}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
