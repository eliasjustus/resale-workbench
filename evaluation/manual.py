"""Retained-file binding for manual economic reviews; no factual certification."""
import hashlib
import json
from pathlib import Path, PurePosixPath

from .privacy import _no_links


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def retained_path(root, value):
    if (not isinstance(value, str) or not value or '\\' in value or ':' in value or
            PurePosixPath(value).is_absolute() or any(p in {'', '.', '..'} for p in value.split('/'))):
        raise ValueError('Retained evidence needs a confined relative file path')
    path = _no_links(root / value)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('Retained evidence file is missing or outside its root')
    return path


def reference_paths(review):
    references = set()
    for comp in review.get('comps', []):
        paths = comp.get('source_paths', [])
        if not isinstance(paths, list) or not all(isinstance(p, str) and p for p in paths):
            raise ValueError('Comparable source_paths must be an array of relative paths')
        references.update(paths)
    return sorted(references)


def verify_capture(capture, record, *, require_gallery=False):
    capture = _no_links(capture)
    photos = record.get('photos', [])
    if not isinstance(photos, list) or (require_gallery and not photos):
        raise ValueError('Supported manual review requires a retained original gallery')
    seen = set()
    for photo in photos:
        index = photo.get('index')
        if type(index) is not int or index < 1 or index in seen or photo.get('status') != 'downloaded':
            raise ValueError('Retained gallery is incomplete or has invalid indices')
        seen.add(index)
        path = retained_path(capture, photo.get('local_path'))
        if not path.is_relative_to(capture / 'photos') or sha(path) != photo.get('sha256'):
            raise ValueError('Original gallery path/hash mismatch')


def seal_review(capture, review_path, evidence_root=None):
    """Hash current referenced files after operator review, without endorsing facts."""
    capture = _no_links(capture)
    review_path = _no_links(review_path)
    review = json.loads(review_path.read_text(encoding='utf-8'))
    if review.get('schema_version') != 2:
        raise ValueError('Manual retained evidence binding requires review schema 2')
    record = json.loads((capture / 'record.json').read_text(encoding='utf-8'))
    if sha(capture / 'record.json') != review.get('source_record_sha256'):
        raise ValueError('Review source hash does not match record.json')
    if (review.get('listing_id'), review.get('capture_id')) != (record.get('listing_id'), record.get('capture_id')):
        raise ValueError('Review capture identity differs')
    verify_capture(capture, record)
    root = _no_links(evidence_root or review_path.parent)
    review['retained_evidence'] = {
        'schema_version': 1, 'root': str(root),
        'files': [{'path': path, 'sha256': sha(retained_path(root, path))} for path in reference_paths(review)],
    }
    return review


def verify_review_files(capture, record, review, review_path, *, supported):
    synthetic = record.get('synthetic') is True and review.get('synthetic') is True
    verify_capture(capture, record, require_gallery=supported and not synthetic)
    references = reference_paths(review)
    binding = review.get('retained_evidence')
    if binding is None:
        if synthetic:
            # Demo references are emitted beside its capture, even if the review
            # is copied elsewhere for a policy experiment.
            root = _no_links(Path(capture))
            for path in references:
                retained_path(root, path)
            return
        if supported:
            raise ValueError('Bind retained evidence with resale seal-review before a supported manual outcome')
        return
    if not isinstance(binding, dict) or set(binding) != {'schema_version', 'root', 'files'} or binding['schema_version'] != 1:
        raise ValueError('Unknown retained evidence binding schema')
    if not isinstance(binding['root'], str) or not binding['root']:
        raise ValueError('Retained evidence root missing')
    root = Path(binding['root'])
    if not root.is_absolute():
        root = Path(review_path).parent / root
    root = _no_links(root)
    files = binding['files']
    if not isinstance(files, list):
        raise ValueError('Retained files must be an array')
    bound = {}
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != {'path', 'sha256'} or not isinstance(entry['path'], str):
            raise ValueError('Invalid retained file binding')
        if entry['path'] in bound:
            raise ValueError('Duplicate retained file binding')
        bound[entry['path']] = entry['sha256']
    if sorted(bound) != references:
        raise ValueError('Retained evidence bindings must cover exactly the declared comparable source files')
    for path, digest in bound.items():
        if sha(retained_path(root, path)) != digest:
            raise ValueError('Retained evidence file hash changed')
