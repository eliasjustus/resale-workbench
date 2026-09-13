"""Source-independent handoff v2. Checks references and declarations, not truth."""
from datetime import datetime, timezone
from pathlib import Path


KINDS = {'transaction', 'modelled_estimate', 'asking_context', 'expert_judgment', 'item_observation'}


def strings(value, nonempty=False):
    return (isinstance(value, list) and (bool(value) or not nonempty)
            and all(isinstance(v, str) and v.strip() for v in value))


def retained(folder, refs, manifest_paths, nonempty=True):
    if not strings(refs, nonempty):
        raise ValueError('Evidence references must be a string array with retained sources')
    for ref in refs:
        path = (folder / ref).resolve()
        if (Path(ref).is_absolute() or not path.is_relative_to(folder.resolve())
                or not path.is_file() or path not in manifest_paths):
            raise ValueError('Evidence reference absent, escaping or outside READY: ' + ref)


def validate_handoff(folder, handoff, manifest_paths):
    try:
        evidence = handoff.get('evidence')
        if not isinstance(evidence, list):
            raise ValueError('evidence must be an array')
        identities = set()
        for item in evidence:
            if not isinstance(item, dict):
                raise ValueError('Evidence record must be an object')
            identity = item.get('evidence_id')
            if not isinstance(identity, str) or not identity.strip() or identity in identities:
                raise ValueError('Evidence IDs must be nonempty and unique within the packet')
            identities.add(identity)
            if item.get('kind') not in KINDS:
                raise ValueError('Evidence kind invalid')
            for field in ('locator', 'observation', 'supports'):
                if not isinstance(item.get(field), str) or not item[field].strip():
                    raise ValueError('Evidence missing ' + field)
            stamp = datetime.fromisoformat(item['captured_at'].replace('Z', '+00:00'))
            if stamp.tzinfo is None or stamp > datetime.now(timezone.utc):
                raise ValueError('Evidence capture time invalid')
            if not strings(item.get('limitations')):
                raise ValueError('Evidence limitations must be an array')
            retained(folder, item.get('source_paths'), manifest_paths)
            retained(folder, item.get('image_paths'), manifest_paths, nonempty=False)
            if not isinstance(item.get('image_inspection'), str) or not item['image_inspection'].strip():
                raise ValueError('Evidence must state image inspection coverage or why unavailable/not relevant')
        research = handoff.get('research', {})
        if not isinstance(research, dict) or not isinstance(research.get('attempts'), list):
            raise ValueError('research.attempts must be an array')
        if not isinstance(research.get('stop_reason'), str) or not research['stop_reason'].strip():
            raise ValueError('Research stop reason required')
        for attempt in research['attempts']:
            if not isinstance(attempt, dict) or any(not isinstance(attempt.get(k), str) or not attempt[k].strip()
                    for k in ('approach', 'result', 'next_decision')):
                raise ValueError('Research attempt needs approach, result and next_decision')
            retained(folder, attempt.get('source_paths'), manifest_paths)
        if handoff.get('outcome') == 'unsupported':
            exclusion = handoff.get('scope_exclusion', {})
            if not isinstance(exclusion.get('reason'), str) or not exclusion['reason'].strip():
                raise ValueError('Unsupported preparation requires evidenced scope exclusion')
            retained(folder, exclusion.get('source_paths'), manifest_paths)
        return {'substantive_ready': True, 'errors': [], 'warnings': [], 'audits': []}
    except (ValueError, KeyError, TypeError, OSError) as exc:
        return {'substantive_ready': False, 'errors': [str(exc)], 'warnings': [], 'audits': []}
