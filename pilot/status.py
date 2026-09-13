"""Read current coordination state without preparing jobs, dispatching or writing reports."""
import json


CASE_ACTIONS = {
    'selected': 'Inspect the original and privacy derivative, then resale run bind.',
    'bound': 'Complete source, posting-time, radius, privacy and price-blind checks, then resale run attest.',
    'attested': 'Prepare a reviewer assignment with resale run job RUN ID reviewer.',
    'reviewer_prepared': 'Reconcile the existing reviewer job; dispatch once externally and register its actual ID.',
    'reviewer_dispatched': 'Wait for the actual reviewer to finish; inspect output before resale run complete-review.',
    'review_done': 'After all selected reviews finish, prepare resale run job RUN ID valuator.',
    'valuator_prepared': 'Reconcile the existing valuator job; dispatch once externally and register its actual ID.',
    'valuator_dispatched': 'Wait for the actual valuator to finish; inspect output before resale run complete-valuation.',
    'complete': 'Apply or inspect the separate retained-evidence economic review; valuation does not authorize purchase.',
    'unresolved': 'Case closed unresolved; retain its missing-evidence explanation.',
    'failed': 'Case stopped after failure; preserve the first attempt.',
    'skipped': 'Case skipped; preserve the recorded reason.',
}


def snapshot(run):
    from . import queue as q
    with q.session(run, read_only=True) as (run, db, manifest):
        preparation_open = not (manifest.get('preparation_deadline') and
                                q.utc(q.now()) >= q.utc(manifest['preparation_deadline']))
        imports = db.execute('SELECT COUNT(*) FROM imports WHERE run=?', (str(run),)).fetchone()[0]
        plan_row = db.execute("SELECT data FROM events WHERE run=? AND action='search_prepared' ORDER BY id LIMIT 1",
                              (str(run),)).fetchone()
        search = 'not_prepared'
        problems = []
        if plan_row:
            try:
                if q.read(run / 'search-plan.json') != json.loads(plan_row['data']):
                    raise ValueError('Frozen search plan changed')
                search = 'prepared'
            except (OSError, ValueError) as exc:
                search = 'invalid'
                problems.append(str(exc))
        discovery = None
        if (run / 'discovery/summary.json').is_file():
            summary = q.read(run / 'discovery/summary.json')
            discovery = {key: summary.get(key) for key in ('stop_reason', 'coverage_complete', 'row_occurrences')}
        cases = []
        rows = list(db.execute('SELECT listing_id,stage,data FROM cases WHERE run=? ORDER BY listing_id', (str(run),)))
        reviewer_jobs = sum('reviewer_job' in json.loads(row['data']) for row in rows)
        reviewer_limit = manifest.get('configuration', {}).get('budget', {}).get('reviewer_job_limit')
        for row in rows:
            data = json.loads(row['data'])
            action = CASE_ACTIONS.get(row['stage'], 'Inspect this unrecognized stage before proceeding.')
            integrity_errors = []
            if 'capture' in data:
                try:
                    q.verify_bound(data, attested='target_files' in data)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    integrity_errors.append(str(exc))
                    action = 'Inspect the integrity failure; do not dispatch from changed evidence.'
            if not integrity_errors and not preparation_open and row['stage'] in {'selected', 'bound', 'attested'}:
                action = 'Preparation expired; close this unprepared case explicitly with resale run stop.'
            elif not integrity_errors and row['stage'] == 'attested' and reviewer_limit is not None and reviewer_jobs >= reviewer_limit:
                action = 'Reviewer preparation limit reached; reconcile existing work or close this case explicitly.'
            cases.append({'listing_id': row['listing_id'], 'stage': row['stage'],
                          'integrity_errors': integrity_errors, 'next_action': action})
        if problems:
            next_action = 'Inspect frozen search-plan integrity; preserve this run.'
        elif cases:
            next_action = 'Follow the per-case actions; existing assignments must be reconciled before dispatch.'
        elif not preparation_open:
            next_action = 'Preparation expired; preserve this run and initialize a new one for new work.'
        elif imports:
            next_action = 'Inspect imported listings and use resale run select or resale run triage.'
        elif discovery:
            next_action = 'Inspect collection limitations, then resale run import-discovery RUN.'
        elif search == 'prepared':
            next_action = 'Use resale search collect RUN only through an authorized collection route.'
        else:
            next_action = 'Inspect native controls, then resale search prepare RUN; external discovery may also be imported.'
        return {'run_path': str(run), 'search': search, 'preparation_open': preparation_open,
                'discovery_imports': imports, 'discovery': discovery, 'cases': cases,
                'integrity_errors': problems, 'next_action': next_action,
                'checks': 'Frozen manifest/playbooks, local search plan and bound target files. Worker output is checked at completion.',
                'purchase_authorized': False, 'network_used': False, 'jobs_started': False}
