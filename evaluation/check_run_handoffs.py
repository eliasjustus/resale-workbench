"""Generic pre-Sol verifier for a current run's Luna output folders.

The run's explicit ``stage-config.json`` binds a stage folder to its case ID and
frozen target input. This intentionally does not infer identity from a folder
name, and it does not judge comparable facts.
"""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from evaluation.evidence import validate_handoff


EXCLUDED_UNMANIFESTED = {"READY.json", "lessons.md"}
NONVISUAL_REASONS = {'aggregate_sale', 'outside_germany', 'wrong_model', 'bundle', 'active_or_relisted'}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _error(errors: list[str], message: str) -> None:
    if message not in errors:
        errors.append(message)


def _explicit_nonvisual_limit(candidate: dict) -> bool:
    """Allow source-only candidates when the reviewer explicitly scoped images out."""
    limit = candidate.get('visual_audit_scope', {})
    return (isinstance(limit, dict) and limit.get('status') == 'not_required'
            and limit.get('reason') in NONVISUAL_REASONS and bool(limit.get('evidence')))


def check_candidate_galleries(folder: Path, handoff: dict, manifest_paths: set[Path]) -> dict:
    """Check gallery references while separating hash readiness from factual adequacy."""
    errors: list[str] = []
    warnings: list[str] = []
    audits: list[dict] = []
    candidates = handoff.get("candidates", [])
    if not isinstance(candidates, list):
        return {"substantive_ready": False, "errors": ["Candidates is not a list"], "warnings": [], "audits": []}

    for position, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            _error(errors, f"Candidate {position} is not an object")
            continue
        raw_paths = candidate.get("gallery_paths", [])
        opened = candidate.get("gallery_opened_indices", [])
        expected = candidate.get("gallery_expected_count")
        if not isinstance(raw_paths, list) or not isinstance(opened, list):
            _error(errors, f"Candidate {position} gallery fields are not lists")
            continue
        path_errors: list[str] = []
        resolved_paths: list[Path] = []
        for raw_path in raw_paths:
            raw = str(raw_path)
            rel = Path(raw)
            path = (folder / rel).resolve()
            if "://" in raw or not inside(path, folder):
                path_errors.append(f"Candidate {position} gallery path escapes packet: {raw}")
                continue
            if not path.is_file():
                path_errors.append(f"Candidate {position} gallery path is absent: {raw}")
                continue
            if path not in manifest_paths:
                path_errors.append(f"Candidate {position} gallery path is omitted from READY manifest: {raw}")
                continue
            resolved_paths.append(path)
        for message in path_errors:
            _error(errors, message)

        expected_count = expected if type(expected) is int and expected > 0 else None
        opened_indices = [item for item in opened if type(item) is int and item > 0]
        if len(opened_indices) != len(opened) or len(set(opened_indices)) != len(opened_indices):
            path_errors.append(f'Candidate {position} opened indices are invalid or duplicated')
        assets = candidate.get('gallery_assets')
        if assets is None:
            # Legacy one-path-per-position contract; multi-version packets must map positions explicitly.
            retained_indices = set(range(1, len(resolved_paths) + 1))
        else:
            retained_indices = set()
            mapped_paths = set()
            if not isinstance(assets, list):
                path_errors.append(f'Candidate {position} gallery_assets is not a list')
                assets = []
            for asset in assets:
                if not isinstance(asset, dict) or type(asset.get('index')) is not int or asset['index'] < 1:
                    path_errors.append(f'Candidate {position} gallery asset position is invalid')
                    continue
                path = (folder / str(asset.get('path', ''))).resolve()
                if path not in resolved_paths:
                    path_errors.append(f'Candidate {position} gallery asset is not a retained manifest-listed file')
                    continue
                retained_indices.add(asset['index'])
                mapped_paths.add(path)
            if mapped_paths != set(resolved_paths):
                path_errors.append(f'Candidate {position} asset map does not cover retained files')
        for message in path_errors:
            _error(errors, message)
        explicit_limit = _explicit_nonvisual_limit(candidate)
        full = (
            not path_errors
            and expected_count is not None
            and expected_count > 0
            and retained_indices == set(range(1, expected_count + 1))
            and sorted(opened_indices) == list(range(1, expected_count + 1))
        )
        if full:
            status = "fully_audited"
        elif explicit_limit and not path_errors:
            status = "limited_source_only"
            warnings.append(f"Candidate {position} is explicitly scope-limited; full gallery audit not claimed")
        else:
            status = "ready_as_incomplete" if not path_errors else "invalid_gallery_references"
            if expected_count != len(retained_indices) or set(opened_indices) != retained_indices:
                _error(
                    errors,
                    f"Candidate {position} opened/retained gallery count inconsistent: "
                    f"expected={expected_count}, opened={len(opened_indices)}, retained={len(retained_indices)}",
                )
            if not explicit_limit:
                _error(errors, f"Candidate {position} is not fully gallery-audited and has no explicit scope limit")
        audits.append({
            "position": position,
            "status": status,
            "expected_count": expected_count,
            "opened_count": len(opened_indices),
            "retained_count": len(retained_indices),
            "retained_asset_versions": len(resolved_paths),
            "explicit_scope_limit": explicit_limit,
        })

    # Empty candidate packets are a valid bounded stop; the run report records
    # the no-sold-evidence outcome separately and must not create valuation.json.
    return {"substantive_ready": not errors, "errors": errors, "warnings": warnings, "audits": audits}


def check_case(run_dir: Path, spec: dict) -> dict:
    """Verify one configured case and return a serializable result."""
    folder_name = spec["folder"]
    expected_case = spec["case_id"]
    folder = (run_dir / spec.get("output", f"stages/{folder_name}/luna-output")).resolve()
    frozen_dir = (run_dir / spec["frozen_input"]).resolve()
    errors: list[str] = []
    ready_path = folder / "READY.json"
    handoff_path = folder / "handoff.json"
    result = {
        "case_id": expected_case,
        "folder": folder_name,
        "ready": False,
        "errors": errors,
        "substantive_ready": False,
        "substantive_errors": [],
        "substantive_warnings": [],
        "gallery_audits": [],
    }
    if not ready_path.exists():
        _error(errors, "READY.json absent")
        return result
    if not handoff_path.exists():
        _error(errors, "handoff.json absent")
        return result
    try:
        ready = json.loads(ready_path.read_text(encoding="utf-8-sig"))
        handoff = json.loads(handoff_path.read_text(encoding="utf-8-sig"))
        if ready.get("case_id") != expected_case or handoff.get("case_id") != expected_case:
            _error(errors, "Wrong case identity")
        if ready.get("handoff_sha256") != sha(handoff_path):
            _error(errors, "Handoff hash mismatch")

        listed: set[Path] = set()
        for entry in ready.get("files", []):
            rel = Path(entry["path"])
            path = (folder / rel).resolve()
            if not inside(path, folder):
                _error(errors, "Manifest path escapes packet: " + rel.as_posix())
                continue
            if path in listed:
                _error(errors, "Duplicate manifest path: " + rel.as_posix())
            listed.add(path)
            if not path.is_file() or sha(path) != entry.get("sha256"):
                _error(errors, "Missing or changed file: " + rel.as_posix())
        actual = {p.resolve() for p in folder.rglob("*") if p.is_file() and p.name not in EXCLUDED_UNMANIFESTED}
        optional_lessons = {p.resolve() for p in folder.rglob('lessons.md') if p.is_file()}
        if not actual.issubset(listed) or not listed.issubset(actual | optional_lessons):
            _error(errors, "Manifest does not cover exact packet file set")

        if handoff.get("role") != "evidence_preparation" or handoff.get("valuation_performed") is not False:
            _error(errors, "Preparation role/valuation declaration invalid")
        if handoff.get("purchase_authorized") is not False or handoff.get("target_price_seen") is not False:
            _error(errors, "Price exposure or purchase declaration invalid")

        target = handoff.get("target", {})
        input_path_text = target.get("input_path")
        if not input_path_text:
            _error(errors, "Target input absent from handoff packet")
            output_input = None
        else:
            output_input = (folder / input_path_text).resolve()
            if not inside(output_input, folder) or not output_input.is_file():
                _error(errors, "Target input path escapes packet or is absent")
                output_input = None
            elif output_input not in listed:
                _error(errors, "Target input is omitted from READY manifest")
            elif target.get("input_sha256") != sha(output_input):
                _error(errors, "Target input hash mismatch")

        frozen_input = frozen_dir / "input.json"
        if not frozen_input.is_file():
            _error(errors, "Frozen target input absent")
        elif output_input is not None and sha(output_input) != sha(frozen_input):
            _error(errors, "Target input differs from frozen input")

        try:
            frozen = json.loads(frozen_input.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            frozen = {}
        try:
            output_packet = json.loads(output_input.read_text(encoding="utf-8")) if output_input else {}
        except (OSError, json.JSONDecodeError):
            output_packet = {}

        frozen_photos = {item["index"]: item for item in frozen.get("photos", []) if item.get("status") == "downloaded"}
        output_photos = {item["index"]: item for item in output_packet.get("photos", []) if item.get("status") == "downloaded"}
        reported_photo_paths = [Path(p) for p in target.get("photo_paths", [])]
        reported_paths = {p.as_posix() for p in reported_photo_paths}
        reported_indices = sorted(target.get("opened_indices", []))
        if reported_indices != sorted(frozen_photos):
            _error(errors, "Reported opened_indices do not cover frozen target photos")
        if set(output_photos) != set(frozen_photos):
            _error(errors, "Output target photo set differs from frozen target photos")
        if len(reported_photo_paths) != len(frozen_photos):
            _error(errors, "Handoff photo_paths do not cover frozen target photos")
        for position, (index, frozen_item) in enumerate(sorted(frozen_photos.items())):
            output_item = output_photos.get(index)
            if position >= len(reported_photo_paths):
                continue
            rel = reported_photo_paths[position]
            photo = (folder / rel).resolve()
            if not inside(photo, folder) or not photo.is_file():
                _error(errors, f"Target photo {index} path escapes packet or is absent")
                continue
            if photo not in listed:
                _error(errors, f"Target photo {index} is omitted from READY manifest")
            output_sha = output_item.get("sha256") if output_item else None
            if sha(photo) != frozen_item.get("sha256") or output_sha != frozen_item.get("sha256"):
                _error(errors, f"Target photo {index} differs from frozen photo")
            if rel.as_posix() not in reported_paths:
                _error(errors, f"Target photo {index} missing from handoff photo_paths")

        text = json.dumps(handoff if handoff.get("schema_version") != 2 else handoff.get("target", {}), ensure_ascii=False)
        if "kleinanzeigen.de/s-anzeige/" in text or "canonical_url" in text:
            _error(errors, "Target source URL leaked into handoff")

        semantic = (validate_handoff(folder, handoff, listed) if handoff.get("schema_version") == 2
                    else check_candidate_galleries(folder, handoff, listed))

        result.update({
            "ready": not errors,
            "ready_sha256": sha(ready_path),
            "files_verified": len(listed),
            "luna_completed_at": ready.get("completed_at") or ready.get("completed_at_utc") or ready.get("completed_utc"),
            "limits": "Checks hashes, file completeness, frozen-input binding and declarations; not proof of visual inspection or factual correctness.",
            "substantive_ready": semantic["substantive_ready"],
            "substantive_errors": semantic["errors"],
            "substantive_warnings": semantic["warnings"],
            "gallery_audits": semantic["audits"],
        })
        return result
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        _error(errors, str(exc))
        return result


def load_config(run_dir: Path) -> dict:
    return json.loads((run_dir / "stage-config.json").read_text(encoding="utf-8"))


def write_result(run_dir: Path, result: dict) -> Path:
    """Preserve the first successful barrier; subsequent checks get recheck files."""
    if result["all_ready"]:
        destination = run_dir / "handoff-check.json"
        if destination.exists():
            index = 1
            while (run_dir / f"handoff-recheck-{index}.json").exists():
                index += 1
            destination = run_dir / f"handoff-recheck-{index}.json"
    else:
        destination = run_dir / "handoff-check-pending.json"
    result["written_to"] = str(destination)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    config = load_config(run_dir)
    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "cases": [check_case(run_dir, spec) for spec in config["luna_cases"]],
    }
    result["all_ready"] = all(item["ready"] for item in result["cases"])
    result["all_substantive_ready"] = all(item.get("substantive_ready", False) for item in result["cases"])
    result["dispatch_ready"] = result["all_ready"] and result["all_substantive_ready"]
    destination = write_result(run_dir, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["dispatch_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
