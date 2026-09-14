"""Bind the unchanged retained-evidence evaluator to immutable operational inputs."""
import hashlib
import json
from pathlib import Path

from evaluation.manual import retained_path, reference_paths, verify_review_files
from evaluation.gates import evaluate as evaluate_values
from evaluation.privacy import _no_links
from evaluation.service import evaluate_capture
from pilot.config import economic_policy

from .clock import parse_timestamp, utc_now
from .contracts import read_record
from .errors import ContractError, IntegrityError, UnresolvedError
from .serialization import canonical_bytes, digest, load_json


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inputs(capture, review_path, review, record):
    paths = {capture / "record.json", review_path}
    for photo in record.get("photos", []):
        if photo.get("local_path"):
            paths.add(retained_path(capture, photo["local_path"]))
    binding = review.get("retained_evidence")
    if binding:
        root = Path(binding["root"])
        root = _no_links(root if root.is_absolute() else review_path.parent / root)
        for reference in binding["files"]:
            paths.add(retained_path(root, reference["path"]))
    else:
        for relative in reference_paths(review):
            paths.add(retained_path(capture,relative))
    return [{"path":str(path),"sha256":_sha(_no_links(path))} for path in sorted(paths)]


def evaluate_bound(capture, review_path, *, record_id, case_id, producer_ref, as_of, synthetic):
    """Run current schema2 only. No config override, replay or capture mutation."""
    try:
        capture, review_path = _no_links(capture), _no_links(review_path)
        raw_record = (capture / "record.json").read_bytes()
        raw_review = review_path.read_bytes()
        record, review = json.loads(raw_record), json.loads(raw_review.decode("utf-8-sig"))
        if type(record) is not dict or type(review) is not dict:
            raise ContractError("Upstream record and review must be objects")
        if type(review.get("schema_version")) is not int or review["schema_version"] != 2:
            raise ContractError("Historical replay cannot be a current operating input")
        if review.get("source_record_sha256") != hashlib.sha256(raw_record).hexdigest():
            raise IntegrityError("Captured review does not bind the original source-record bytes")
        if parse_timestamp(as_of).date().isoformat() != review.get("as_of"):
            raise UnresolvedError("Operating view needs a current review for its as-of date")
        if type(synthetic) is not bool or type(record.get("synthetic",False)) is not bool or type(review.get("synthetic",False)) is not bool or record.get("synthetic",False) != review.get("synthetic",False) or review.get("synthetic",False) != synthetic:
            raise IntegrityError("Upstream capture/review synthetic boundary mismatch")
        before = _inputs(capture,review_path,review,record)
        # Compute the expected result from our already-read immutable values,
        # and validate their retained bindings. A transient path replacement
        # during evaluate_capture cannot bind different arithmetic to these bytes.
        expected = evaluate_values(record,review,policy=economic_policy(review.get("economic_policy")))
        if synthetic:
            from resale_tool.demo import NOTICE
            expected.update(synthetic=True,notice=NOTICE)
        verify_review_files(capture,record,review,review_path,supported=expected["outcome"] == "supported")
        result = evaluate_capture(capture,review_path)
        if result != expected:
            raise IntegrityError("Upstream result differs from the captured immutable review values")
        verify_review_files(capture,record,review,review_path,supported=expected["outcome"] == "supported")
        if _inputs(capture,review_path,review,record) != before or (capture/"record.json").read_bytes() != raw_record or review_path.read_bytes() != raw_review:
            raise IntegrityError("Upstream retained input changed during evaluation")
        snapshot = {"record_type":"upstream_evaluation","schema_version":1,"record_id":record_id,"created_at":utc_now(),"synthetic":synthetic,"producer_ref":producer_ref,"case_id":case_id,"as_of":as_of,"capture_path":str(capture),"review_path":str(review_path),"capture_sha256":hashlib.sha256(raw_record).hexdigest(),"review_sha256":hashlib.sha256(raw_review).hexdigest(),"result_sha256":digest(result),"economic_policy_sha256":digest(result["economic_policy"],domain="policy"),"result_json":canonical_bytes(result).decode("utf-8"),"retained_inputs":before,"replay_only":False,"purchase_authorized":False}
        return read_record(snapshot,expected_type="upstream_evaluation")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc,(ContractError,IntegrityError,UnresolvedError)):
            raise
        raise IntegrityError("Current upstream evaluation or retained input binding failed") from exc


def read_result(snapshot):
    snapshot = read_record(snapshot,expected_type="upstream_evaluation")
    result = load_json(snapshot["result_json"])
    if type(result) is not dict or digest(result) != snapshot["result_sha256"] or result.get("replay_only") or result.get("purchase_authorized") is not False or result.get("outcome") not in {"supported","unsupported","unresolved"}:
        raise IntegrityError("Upstream result is changed, historical or not a current evaluation")
    if digest(result.get("economic_policy"),domain="policy") != snapshot["economic_policy_sha256"]:
        raise IntegrityError("Upstream economic policy digest mismatch")
    return result


def recheck(snapshot):
    """Re-evaluate at the frozen view, comparing every bound byte and result."""
    snapshot = read_record(snapshot,expected_type="upstream_evaluation")
    read_result(snapshot)
    current = evaluate_bound(snapshot["capture_path"],snapshot["review_path"],record_id=snapshot["record_id"],case_id=snapshot["case_id"],producer_ref=snapshot["producer_ref"],as_of=snapshot["as_of"],synthetic=snapshot["synthetic"])
    if any(current[key] != snapshot[key] for key in snapshot if key != "created_at"):
        raise IntegrityError("Upstream evaluation lineage no longer matches")
    return read_result(current)
