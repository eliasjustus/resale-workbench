"""Entirely fictional fixtures exercising the production economic gates offline."""

import hashlib
import html
import json
from pathlib import Path

from evaluation.gates import evaluate


NOTICE = 'SYNTHETIC DEMO: all items, transactions, inspection statements and costs are invented. No market evidence or purchase advice.'


def write_json(path, value):
    with Path(path).open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write('\n')


def sample(case_id, ask, unresolved=False):
    record = {'synthetic': True, 'notice': NOTICE, 'listing_id': case_id,
              'capture_id': 'synthetic-capture-v1', 'collection_status': 'complete',
              'fields': {'title': {'value': 'Fictional Example Device'},
                         'asking_price': {'value': f'{ask} €'}}}
    review = {'schema_version': 2, 'synthetic': True, 'notice': NOTICE,
              'listing_id': case_id, 'capture_id': record['capture_id'],
              'as_of': '2026-01-15', 'review_mode': 'synthetic_offline_demo',
              'scope': {'status': 'included', 'evidence': ['Fictional in-scope item']},
              'work': {'required': False, 'evidence': ['Fictional completed function inspection']},
              'contradictions': [],
              'acquisition_price': {'eur': str(ask), 'source_text': f'{ask} €'},
              'costs': {key: {'eur': '2', 'basis': 'Invented demo cost only'} for key in (
                  'acquisition_transport', 'resale_shipping', 'fees', 'packaging', 'defect_allowance')},
              'transaction_adequacy': {'status': 'sufficient',
                  'evidence_ids': ['demo-sale-1', 'demo-sale-2'],
                  'reason': 'Invented exact matches demonstrate the declaration contract only.',
                  'limitations': ['Fictional transactions; no actual evidence or model judgment.']},
              'comps': [{'evidence_id': f'demo-sale-{i}', 'kind': 'transaction',
                        'source_paths': ['synthetic-evidence.txt'],
                        'locator': f'fictional://demo/sale/{i}',
                        'captured_at': '2026-01-15T12:00:00Z',
                        'units_sold': 1, 'review_status': 'accepted',
                        'review_evidence': ['Invented audited exact current-condition match'],
                        'condition_basis': 'current_condition', 'price_basis': 'actual_sold_item_price',
                        'currency': 'EUR', 'seller_country': 'DE', 'sold_at': '2026-01-01',
                        'item_price_eur': str(price)} for i, price in enumerate((100, 110), 1)]}
    for key in ('identity', 'condition', 'gallery'):
        review[key] = {'status': 'resolved', 'evidence': ['Fictional inspection declaration']}
    if unresolved:
        review['condition'] = {'status': 'unresolved', 'evidence': []}
        review['costs']['defect_allowance'] = {'eur': None, 'basis': None}
    return record, review


def create_demo(destination):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    results = []
    for case_id, ask, unresolved in (('supported-example', 50, False),
                                     ('unsupported-example', 90, False),
                                     ('unresolved-example', 50, True)):
        folder = destination / case_id
        folder.mkdir()
        record, review = sample(case_id, ask, unresolved)
        write_json(folder / 'record.json', record)
        review['source_capture'] = '.'
        review['source_record_sha256'] = hashlib.sha256((folder / 'record.json').read_bytes()).hexdigest()
        write_json(folder / 'review.json', review)
        (folder / 'synthetic-evidence.txt').write_text(NOTICE + '\nTwo fictional same-condition sales: EUR100 and EUR110.\n', encoding='utf-8')
        result = evaluate(record, review)
        result.update(synthetic=True, notice=NOTICE)
        write_json(folder / 'result.json', result)
        results.append(result)
    rows = []
    for result in results:
        case_id = result['listing_id']
        arithmetic = result['arithmetic'] or {}
        rows.append('<tr><td>' + html.escape(case_id) + '</td><td><strong>' +
                    html.escape(result['outcome']) + '</strong></td><td>' +
                    html.escape(arithmetic.get('margin_eur', 'unknown')) + '</td><td>' +
                    html.escape(', '.join(result['reasons'])) + '</td><td>' +
                    f'<a href="{case_id}/review.json">Review inputs</a> · '
                    f'<a href="{case_id}/result.json">Gate output</a></td></tr>')
    page = '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>Resale Workbench - synthetic demo</title><style>
body{font:17px/1.6 system-ui,sans-serif;max-width:1100px;margin:3rem auto;padding:0 1.2rem;color:#172b39;background:#f6f8fa}
h1{line-height:1.2} .notice{background:#fff1cc;padding:1rem;border-left:5px solid #a86600}
table{border-collapse:collapse;width:100%;background:white}th,td{text-align:left;padding:1rem;border-bottom:1px solid #ddd}
a{color:#065c99}code{background:#e8edf0;padding:.15rem}.table{overflow-x:auto}
</style><h1>Resale Workbench: three evidence outcomes</h1><p class="notice">''' + html.escape(NOTICE) + '''</p>
<p>The production deterministic gates evaluated each fictional case. The gate verifies declarations and arithmetic; it cannot establish that a transaction or inspection really happened.</p>
<div class="table"><table><tr><th>Case</th><th>Outcome</th><th>Margin (EUR)</th><th>Reason</th><th>Inspect</th></tr>''' + ''.join(rows) + '''</table></div>
<p>The first two examples use a EUR100 minimum fictional sale and EUR10 fictional costs. Acquiring for EUR50 leaves EUR40; EUR90 leaves EUR0. The third case lacks condition evidence and an established defect allowance, so arithmetic is withheld.</p>
<p>The default example floor is EUR20 plus any justified work allowance. Supported means the declared evidence meets the gate; every purchase still needs a human decision.</p>
<h2>Try a manual case</h2><ol><li>Use a retained capture folder containing record.json.</li>
<li>Run <code>resale draft-review CAPTURE --output review.json</code>.</li>
<li>Inspect original evidence and complete the review; leave unknowns unresolved.</li>
<li>Bind retained comparable files with <code>resale seal-review CAPTURE review.json --evidence-root EVIDENCE --output review-sealed.json</code>.</li>
<li>Run <code>resale evaluate CAPTURE review-sealed.json --output result.json</code>.</li></ol>
<p>Edit copies of review inputs, then evaluate into a new result file. Original evidence and previous outputs are preserved.</p></html>'''
    (destination / 'review.html').write_text(page, encoding='utf-8')
    write_json(destination / 'summary.json', {'synthetic': True, 'notice': NOTICE, 'cases': results})
    return destination / 'review.html'
