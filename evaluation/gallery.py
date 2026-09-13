"""Bind saved eBay assets to observed DOM image labels, never filename order."""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit


def image_key(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname != 'i.ebayimg.com':
        raise ValueError('Expected an observed eBay image URL')
    match = re.fullmatch(r'/images/g/([^/]+)/[^/]+', parsed.path)
    if not match:
        raise ValueError('Unrecognized image path')
    return match.group(1)


def reconcile_positions(observed_images, retained_assets):
    """Input DOM rows have alt/src; saved asset rows have url plus file/hash metadata.

    URLs identify already observed files only. This helper never fetches an asset,
    claims visual inspection, or constructs a download URL.
    """
    keys = {}; positions = {}; totals = set()
    for row in observed_images:
        match = re.search(r'\bBild (\d+) von (\d+)$', row.get('alt') or '')
        if not match or not row.get('src'):
            continue
        position, total = map(int, match.groups())
        if position < 1 or position > total:
            raise ValueError('Invalid observed position')
        key = image_key(row['src']); totals.add(total)
        if key in keys and keys[key] != position or position in positions and positions[position] != key:
            raise ValueError('Conflicting image-to-position binding')
        keys[key] = position; positions[position] = key
    if len(totals) != 1:
        raise ValueError('Missing or conflicting gallery size')
    expected = totals.pop()
    if set(positions) != set(range(1, expected+1)):
        raise ValueError('DOM position mapping incomplete; load remaining controls first')
    mapped = []
    for row in retained_assets:
        key = image_key(row['url'])
        if key not in keys:
            raise ValueError('Saved asset has no observed position binding')
        mapped.append({**row, 'position': keys[key]})
    retained = sorted({r['position'] for r in mapped})
    return {'expected_count':expected,'retained_positions':retained,
            'all_positions_retained':retained == list(range(1,expected+1)),
            'assets':sorted(mapped,key=lambda r:r['position']),
            'inspection_verified':False}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('observed',type=Path,help='JSON array of DOM alt/src rows')
    parser.add_argument('assets',type=Path,help='JSON array of retained asset records with url')
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    if args.output.exists():
        parser.error('Output already exists; preserve original evidence')
    read=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
    args.output.write_text(json.dumps(reconcile_positions(read(args.observed),read(args.assets)),indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
