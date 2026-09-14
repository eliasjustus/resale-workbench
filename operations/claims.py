"""Retained bytes plus explicit substantive review; hashes alone never prove facts."""
import ctypes
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat

from evaluation.privacy import _no_links

from .clock import parse_timestamp
from .contracts import read_record, record_digest
from .errors import IntegrityError, UnresolvedError
from .verification import read_verification


def _identity(info):
    # Windows Python versions can expose different ctime meanings through stat
    # and fstat. Compare file identity/size/mtime across APIs; compare ctime only
    # between reads of the same open handle below.
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _handle_path(stream):
    if os.name == "nt":
        import msvcrt
        function = ctypes.windll.kernel32.GetFinalPathNameByHandleW
        function.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
        function.restype = ctypes.c_uint32
        size = function(msvcrt.get_osfhandle(stream.fileno()), None, 0, 0)
        if not size:
            raise UnresolvedError("Host cannot verify the retained-file handle path")
        buffer = ctypes.create_unicode_buffer(size + 1)
        length = function(msvcrt.get_osfhandle(stream.fileno()), buffer, size + 1, 0)
        if not length or length > size:
            raise UnresolvedError("Host returned an unstable retained-file handle path")
        value = buffer.value
        if value.startswith("\\\\?\\UNC\\"):
            return Path("\\\\" + value[8:])
        return Path(value[4:] if value.startswith("\\\\?\\") else value)
    path = Path(f"/proc/self/fd/{stream.fileno()}")
    if path.exists():
        return Path(os.readlink(path))
    raise UnresolvedError("This host has no qualified retained-file handle-path verification")


def verify_retained_file(root, reference):
    """Read one regular file through a bound handle and detect replacement.

    The source locator is preserved for human interpretation, never executed.
    Ancestor identities and the final handle path are rechecked around the read.
    """
    if type(reference) is not dict or set(reference) != {"relative_path", "sha256", "locator"}:
        raise IntegrityError("Malformed retained source reference")
    relative = reference["relative_path"]
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative or PurePosixPath(relative).is_absolute() or any(part in {"", ".", ".."} for part in relative.split("/")):
        raise IntegrityError("Retained source path is not confined")
    try:
        root = _no_links(root)
        path = _no_links(root / relative)
        if not path.is_relative_to(root):
            raise IntegrityError("Retained source escaped its root")
        ancestors = {parent:(parent.stat().st_dev, parent.stat().st_ino) for parent in path.parents}
        expected = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(expected.st_mode):
            raise IntegrityError("Retained evidence must be a regular file")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if _identity(opened) != _identity(expected) or os.path.normcase(str(_handle_path(stream))) != os.path.normcase(str(path)):
                raise IntegrityError("Retained file was replaced before opening")
            hasher = hashlib.sha256()
            for block in iter(lambda:stream.read(1024 * 1024), b""):
                hasher.update(block)
            finished = os.fstat(stream.fileno())
            if _identity(finished) != _identity(opened) or finished.st_ctime_ns != opened.st_ctime_ns:
                raise IntegrityError("Retained file changed while reading")
        if _no_links(path) != path or _identity(path.stat(follow_symlinks=False)) != _identity(opened):
            raise IntegrityError("Retained file path changed while reading")
        for parent, identity in ancestors.items():
            current = parent.stat(follow_symlinks=False)
            if (current.st_dev, current.st_ino) != identity:
                raise IntegrityError("Retained source ancestor was replaced")
        if hasher.hexdigest() != reference["sha256"]:
            raise IntegrityError("Retained source digest mismatch")
        return {"relative_path":relative, "sha256":hasher.hexdigest(), "locator":reference["locator"]}
    except FileNotFoundError as exc:
        raise UnresolvedError("Retained source is unavailable") from exc
    except (OSError, ValueError) as exc:
        if isinstance(exc, (IntegrityError, UnresolvedError)):
            raise
        raise IntegrityError("Retained source could not be safely verified") from exc


def verify_claim(claim, *, root, reviews, tombstones=(), as_of, synthetic, review_authority=None):
    """Assess current retained evidence and bound review decisions.

    review_authority(review, claim, as_of=...) must qualify substantive reviewer
    competence and current actor scope. No default actor string is authoritative.
    Without that host service, a matching digest remains unresolved.
    """
    claim = read_record(claim, expected_type="material_claim")
    instant = parse_timestamp(as_of)
    reasons, failures = [], []
    if claim["synthetic"] != synthetic:
        failures.append("synthetic_boundary_mismatch")
    if parse_timestamp(claim["created_at"]) > instant:
        reasons.append("claim_not_available_as_of")
    if claim["status"] == "contradicted":
        failures.append("claim_contradicted")
    elif claim["status"] != "established":
        reasons.append("claim_not_established")
    if not claim["source_refs"]:
        reasons.append("retained_reference_missing")
    retained = []
    for value in tombstones:
        tombstone = read_record(value, expected_type="evidence_tombstone")
        if tombstone["synthetic"] != synthetic or parse_timestamp(tombstone["created_at"]) > instant or parse_timestamp(tombstone["deleted_at"]) > instant:
            continue
        deleted_files = {(reference["relative_path"],reference["sha256"]) for reference in tombstone["source_refs"]}
        if claim["record_id"] in tombstone["affected_claim_ids"] or any((reference["relative_path"],reference["sha256"]) in deleted_files for reference in claim["source_refs"]):
            reasons.append("retained_source_deleted")
    for reference in claim["source_refs"]:
        try:
            retained.append(verify_retained_file(root, reference))
        except UnresolvedError:
            reasons.append("retained_source_unavailable")
        except IntegrityError:
            failures.append("retained_source_integrity_failure")
    applicable = []
    for value in reviews:
        review = read_record(value, expected_type="claim_review")
        if review["claim_id"] != claim["record_id"] or parse_timestamp(review["created_at"]) > instant or parse_timestamp(review["reviewed_at"]) > instant:
            continue
        if review["synthetic"] != synthetic or review["claim_sha256"] != record_digest(claim) or review["source_refs"] != claim["source_refs"]:
            failures.append("review_binding_mismatch")
            continue
        applicable.append(review)
        if review["conclusion"] == "contradicted":
            failures.append("substantive_review_contradiction")
        elif review["conclusion"] != "established":
            reasons.append("substantive_review_unresolved")
        if review["conflicting_claim_ids"]:
            reasons.append("conflicting_claims_unresolved")
        if not review["competence_claim_ids"]:
            reasons.append("reviewer_competence_unresolved")
        if review_authority is None or read_verification(review_authority(review, claim, as_of=as_of))["state"] != "pass":
            reasons.append("reviewer_authority_unresolved")
    if not applicable:
        reasons.append("substantive_review_missing")
    return {"state":"fail" if failures else "unknown" if reasons else "pass", "claim_id":claim["record_id"], "claim_sha256":record_digest(claim), "subject":claim["subject"], "reason_codes":list(dict.fromkeys(failures+reasons)), "retained_refs":retained, "review_ids":[review["record_id"] for review in applicable], "limitations":list(claim["limitations"]), "review_limitations":[{"review_id":review["record_id"],"limitations":list(review["limitations"])} for review in applicable], "purchase_authorized":False}
