"""Produce a deliberately small support summary without exporting a run archive."""
from collections import Counter
import json
from pathlib import Path
import sqlite3


STAGES = {'selected', 'bound', 'attested', 'reviewer_prepared', 'reviewer_dispatched',
          'review_done', 'valuator_prepared', 'valuator_dispatched', 'complete',
          'failed', 'skipped', 'unresolved'}
OUTCOMES = {'supported', 'unsupported', 'unresolved'}


def summary(run):
    run = Path(run).resolve()
    pointer = json.loads((run / 'pilot.json').read_text(encoding='utf-8-sig'))
    state = Path(pointer['state'])
    if not state.is_absolute():
        state = run / state
    # Read-only connection: unlike queue.connect this cannot migrate or create state.
    db = sqlite3.connect(state.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        row = db.execute('SELECT manifest FROM runs WHERE run=?', (str(run),)).fetchone()
        if row is None:
            raise ValueError('Run is absent from the state database')
        manifest = json.loads(row[0])
        cases = db.execute('SELECT stage,data FROM cases WHERE run=?', (str(run),)).fetchall()
        sightings = db.execute('SELECT count(*) FROM sightings WHERE run=?', (str(run),)).fetchone()[0]
    finally:
        db.close()
    stages = Counter(stage if stage in STAGES else 'unknown' for stage, _ in cases)
    outcomes = Counter()
    for _, data in cases:
        outcome = json.loads(data).get('outcome')
        outcomes[outcome if isinstance(outcome, str) and outcome in OUTCOMES else 'not_recorded'] += 1
    workflow = manifest.get('workflow_version')
    return {'schema_version': 1, 'kind': 'minimal_support_summary',
            'workflow_version': workflow if type(workflow) is int and workflow in {1, 2} else None,
            'counts': {'sightings': sightings, 'cases': len(cases),
                       'stages': dict(sorted(stages.items())), 'outcomes': dict(sorted(outcomes.items()))},
            'contains_raw_evidence': False,
            'limitations': ['Counts describe local records, not independent factual verification.',
                           'No listing IDs, locations, filenames, timestamps, model prompts or source text are exported.',
                           'Review the summary before choosing whether to share it.']}
