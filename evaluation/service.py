"""Shared manual-review application service used by both command-line entry points."""
from datetime import date
import hashlib
import json
from pathlib import Path

from .gates import evaluate
from .manual import verify_review_files
from pilot.config import economic_policy, load_config


def resolve_policy(config_path=None, run_path=None):
    if config_path and run_path:
        raise ValueError('Choose either a config or a frozen run policy.')
    if config_path:
        return load_config(config_path)['economics']
    if run_path:
        from pilot.queue import session
        with session(run_path) as (_, _, manifest):
            if 'configuration' not in manifest:
                raise ValueError('Run has no frozen configuration; omit --run to use the default policy.')
            return manifest['configuration']['economics']
    return economic_policy(None)


def draft_review(capture, config_path=None, run_path=None):
    capture = Path(capture).resolve()
    raw = (capture / 'record.json').read_bytes()
    record = json.loads(raw)
    review = {'schema_version': 2, 'listing_id': record['listing_id'],
              'capture_id': record['capture_id'], 'source_capture': str(capture),
              'source_record_sha256': hashlib.sha256(raw).hexdigest(),
              'as_of': date.today().isoformat(), 'review_mode': 'manual',
              'economic_policy': resolve_policy(config_path, run_path),
              'scope': {'status': 'unresolved', 'evidence': []},
              'work': {'required': None, 'evidence': []}, 'contradictions': [],
              'acquisition_price': {'eur': None,
                  'source_text': record.get('fields', {}).get('asking_price', {}).get('value')},
              'costs': {key: {'eur': None, 'basis': None} for key in (
                  'acquisition_transport', 'resale_shipping', 'fees', 'packaging', 'defect_allowance')},
              'comps': [], 'transaction_adequacy': {'status': 'unresolved',
                  'evidence_ids': [], 'reason': '', 'limitations': []},
              'repair': {'schema_version': 1, 'status': 'unresolved', 'symptoms': [],
                  'hypotheses': [], 'diagnostics': [], 'diagnosis_evidence': [],
                  'verification_evidence': [], 'downside': {'status': 'unresolved', 'evidence_refs': []},
                  'unknowns': ['Inspect the current condition and repair requirements.']}}
    for name in ('identity', 'condition', 'gallery'):
        review[name] = {'status': 'unresolved', 'evidence': []}
    return review


def evaluate_capture(capture, review_path, config_path=None, run_path=None, *, legacy_replay=False):
    """Validate current retained evidence, or explicitly replay legacy declarations.

    Historical replay retains any policy already recorded in the review, using
    the old default when absent, and checks the source-record hash. It is never
    a substitute for the current retained-file workflow.
    """
    capture, review_path = Path(capture).resolve(), Path(review_path).resolve()
    raw = (capture / 'record.json').read_bytes()
    record = json.loads(raw)
    review = json.loads(review_path.read_text(encoding='utf-8-sig'))
    if not isinstance(review, dict):
        raise ValueError('Review must be a JSON object')
    version = review.get('schema_version', 1)
    if type(version) is not int or version not in (1, 2):
        raise ValueError('Unknown economic review schema version')
    if legacy_replay:
        if version != 1:
            raise ValueError('--legacy-replay is restricted to schema-1 historical reviews')
        if config_path or run_path:
            raise ValueError('Legacy replay uses the recorded policy or historical default policy; do not supply --config or --run')
    elif version != 2:
        raise ValueError('Current evaluation requires schema 2; use --legacy-replay only to inspect a schema-1 historical review')
    if review.get('source_record_sha256') != hashlib.sha256(raw).hexdigest():
        raise ValueError('Review source hash does not match record.json; preserve the original capture.')
    if legacy_replay:
        frozen = review.get('economic_policy')
        result = evaluate(record, review, policy=economic_policy(frozen) if frozen is not None else None)
        result.update(replay_only=True, validation_scope='legacy_record_hash_and_declarations_only')
        return result
    frozen = review.get('economic_policy')
    policy = (resolve_policy(config_path, run_path) if config_path or run_path
              else economic_policy(frozen))
    if frozen is not None and economic_policy(frozen) != policy:
        raise ValueError('Selected policy differs from the manual review frozen policy; create a new review.')
    result = evaluate(record, review, policy=policy)
    verify_review_files(capture, record, review, review_path, supported=result['outcome'] == 'supported')
    if review.get('synthetic'):
        from resale_tool.demo import NOTICE
        result.update(synthetic=True, notice=NOTICE)
    return result
