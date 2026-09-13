"""Pure presentation helpers for pilot reports; no database or file access."""
from collections import Counter
from pathlib import Path


def render_report(result):
    manifest = result['manifest']
    cases = result['cases']
    sightings = result['sightings']
    imports = result['discovery_summaries']
    def prose(value):
        text = ' '.join(str(value).split())[:400]
        for character in ('\\', '*', '_', '`', '[', ']', '<', '>', '|', '#'):
            text = text.replace(character, '\\' + character)
        return text
    lines = [f'# Pilot {prose(manifest["run_id"])}', '',
             f'Window: {manifest["window_start_inclusive"]} inclusive to '
             f'{manifest["window_end_exclusive"]} exclusive.',
             f'Scope: {prose(manifest["focus"])}; sampled category: '
             f'{prose(manifest.get("sampled_category") or "not recorded")}; '
             f'Search center: {prose(manifest.get("center") or "unconfigured")}; radius: '
             f'{prose(manifest.get("radius_km"))} km; sold research Germany-wide.', '',
             f'{len(sightings)} row occurrences; {result["unique_listing_ids"]} unique listing IDs; '
             f'{len(cases)} selected cases.', '', 'Discovery coverage:', '']
    for index, summary in enumerate(imports, 1):
        lines.append(f'- Import {index}: coverage_complete={prose(summary.get("coverage_complete"))}; '
                     f'stop_reason={prose(summary.get("stop_reason", "see JSON lane summaries"))}.')
    if not imports:
        lines.append('- No discovery imported.')
    lines.extend(['', 'Triage (not economic evaluations):', ''])
    lines.extend(f"- {d['listing_id']}: {d['disposition']}. {prose(d['reason'])}" for d in result['triage'])
    lines.append('- Remaining eligible unselected rows are unreviewed, not rejected.')
    lines.extend(['', 'Cases:', ''])
    for case in cases:
        lines.append(f'- {case["listing_id"]}: {case["stage"]}; outcome {case["outcome"]}. '
                     f'{prose(case["reason"] or "Work pending.")}')
        if case['carryover_from_run']:
            lines.append('  Deferred carryover from ' + prose(case['carryover_from_run']) + '; not a fresh discovery.')
        if case['integrity_errors']:
            lines.append('  Integrity failure: ' + prose('; '.join(case['integrity_errors'])))
    if not cases:
        lines.append('- No cases selected.')
    lines.extend(['', 'No purchase authorization. No active scheduling. Mechanical checks are not factual validation.',
                  '', 'Limits:', ''])
    lines.extend('- ' + prose(limit) for limit in result['limits'])
    lines.extend(['', 'Complete row accounting and source summaries: pilot-report.json.', ''])
    return '\n'.join(lines)


def compact_report(result, run):
    return {'report_path': str(Path(run).resolve() / 'pilot-report.json'),
            'readable_report_path': str(Path(run).resolve() / 'pilot-report.md'),
            'row_occurrences': result['row_occurrences'],
            'unique_listing_ids': result['unique_listing_ids'],
            'dispositions': dict(Counter(row['disposition'] for row in result['sightings'])),
            'cases': result['cases'], 'purchase_authorized': False, 'automation_active': False}
