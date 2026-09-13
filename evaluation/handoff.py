"""Generate deterministic handoff manifests; facts/inspection remain agent judgments."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _prose(value):
    """Render untrusted index values as inert, single-line Markdown text."""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = ' '.join(value.split())
    for character in ('\\', '`', '*', '_', '{', '}', '[', ']', '<', '>', '(', ')', '!', '|', '#'):
        text = text.replace(character, '\\' + character)
    return text


def render_index(handoff):
    """Index declarations and retained references, without judging their truth.

    This is deliberately not a second prose assessment. Facts, observations and
    inspection declarations remain in handoff.json and their retained sources.
    """
    target = handoff.get('target', {})
    target = target if isinstance(target, dict) else {}
    research = handoff.get('research', {})
    research = research if isinstance(research, dict) else {}
    photos = target.get('photo_paths', [])
    photo_count = str(len(photos)) if isinstance(photos, list) else 'invalid declaration; inspect JSON'
    lines = ['# Packet index', '',
             'Generated from handoff.json. This index does not independently verify facts, '
             'inspection, evidence categories or valuation. Read the JSON and retained sources.', '',
             f'Case: {_prose(handoff.get("case_id", "not declared"))}',
             f'Handoff schema: {_prose(handoff.get("schema_version", "not declared"))}',
             f'Declared outcome: {_prose(handoff.get("outcome", "not declared"))}', '',
             '## Target references', '',
             f'- Input: {_prose(target.get("input_path", "not declared"))}',
             f'- Listed target photos: {photo_count}',
             '- Inspect the target input for privacy derivative flags and redaction limitations. '
             'Supplied images may be derivatives; this index does not establish original-image access.', '',
             '## Evidence references', '',
             'Kinds below are the preparer’s declarations. A modelled estimate, asking context, '
             'expert judgment or observation is not an actual sold transaction.', '']
    evidence = handoff.get('evidence', [])
    if isinstance(evidence, list) and evidence:
        for item in evidence:
            if not isinstance(item, dict):
                lines.append('- Invalid evidence entry; inspect and correct handoff.json.')
                continue
            lines.append(f'- ID: {_prose(item.get("evidence_id", "not declared"))}; '
                         f'declared kind: {_prose(item.get("kind", "not declared"))}.')
            lines.append(f'  Sources: {_prose(item.get("source_paths", []))}')
            lines.append(f'  Images: {_prose(item.get("image_paths", []))}')
            lines.append(f'  Declared limitations: {_prose(item.get("limitations", []))}')
    elif evidence is not None and not isinstance(evidence, list):
        lines.append('- Invalid evidence array; inspect and correct handoff.json.')
    else:
        lines.append('- No evidence entries are indexed. This does not establish that research was complete.')
    candidates = handoff.get('candidates', [])
    if isinstance(candidates, list) and candidates:
        lines.extend(['', '## Legacy candidate references', '',
                      'These are candidate declarations, not a generated finding of comparable transactions.', ''])
        for candidate in candidates:
            if not isinstance(candidate, dict):
                lines.append('- Invalid candidate entry; inspect and correct handoff.json.')
                continue
            lines.append(f'- Candidate: {_prose(candidate.get("sale_id", "not declared"))}; '
                         f'reported source: {_prose(candidate.get("price_record", "not declared"))}.')
    lines.extend(['', '## Remaining review', '',
                  f'- Recorded research stop reason: {_prose(research.get("stop_reason", "not declared"))}',
                  '- Review target unknowns, repair evidence, research gaps and evidence limitations in handoff.json.',
                  '- READY.json binds files and declarations; hashes do not prove factual accuracy.',
                  '- This packet does not authorize a purchase.', ''])
    return '\n'.join(lines)


def finalize(folder):
    folder = Path(folder).resolve()
    index_path = folder / 'HANDOFF.md'
    if index_path.exists() and (not index_path.is_file() or not index_path.resolve().is_relative_to(folder)):
        raise ValueError('Existing HANDOFF.md is not a file inside the packet')
    finalized = (folder / 'READY.json').exists()
    if finalized and not index_path.is_file():
        previous = json.loads((folder / 'READY.json').read_text(encoding='utf-8-sig'))
        if any(item.get('path') == 'HANDOFF.md' for item in previous.get('files', [])):
            raise ValueError('Finalized packet is missing HANDOFF.md; restore its original index or use a new packet')
    handoff = json.loads((folder / 'handoff.json').read_text(encoding='utf-8-sig'))
    if not isinstance(handoff, dict):
        raise ValueError('handoff.json must be an object')
    target = handoff.get('target', {})
    if target.get('input_path'):
        target_input = (folder / target['input_path']).resolve()
        if not target_input.is_relative_to(folder):
            raise ValueError('Target input escapes packet')
        packet = json.loads(target_input.read_text(encoding='utf-8-sig'))
        target['input_sha256'] = sha(target_input)
        target['photo_paths'] = []
        for photo in packet.get('photos', []):
            if photo.get('status') == 'downloaded':
                path = (target_input.parent / photo['path']).resolve()
                if not path.is_relative_to(folder) or sha(path) != photo['sha256']:
                    raise ValueError('Target photo escapes packet or fails input hash')
                target['photo_paths'].append(path.relative_to(folder).as_posix())
        (folder / 'handoff.json').write_text(json.dumps(handoff, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    if not index_path.exists() and not finalized:
        # Never replace authored prose, nor silently refresh an existing index
        # after later JSON edits. READY covers the actual retained index bytes.
        with index_path.open('x', encoding='utf-8', newline='\n') as handle:
            handle.write(render_index(handoff))
    files = []
    for path in sorted(folder.rglob('*')):
        if path.is_file() and path.name not in {'READY.json', 'lessons.md'}:
            if not path.resolve().is_relative_to(folder):
                raise ValueError('File escapes handoff directory')
            files.append({'path': path.relative_to(folder).as_posix(), 'sha256': sha(path)})
    ready = {'schema_version': 1, 'case_id': handoff['case_id'],
             'completed_at_utc': datetime.now(timezone.utc).isoformat(),
             'handoff_sha256': sha(folder / 'handoff.json'), 'files': files}
    (folder / 'READY.json').write_text(json.dumps(ready, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return ready


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    args = parser.parse_args()
    result = finalize(args.folder)
    print(json.dumps({'case_id': result['case_id'], 'files': len(result['files']),
                      'ready_sha256': sha(args.folder / 'READY.json')}))
