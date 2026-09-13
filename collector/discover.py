"""Bounded public search-page collection; follows observed next links only."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
import unicodedata
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from .browser import sync_playwright


from .eligibility import eligibility

POSTING_LABEL_SELECTOR = 'svg[data-title="calendarOutline"] + span'


def parse_search(html, url, observed_at, start, end):
    soup = BeautifulSoup(html, 'html.parser')
    rows = []
    for article in soup.select('article[data-adid]'):
        text = article.get_text('\n', strip=True)
        title = article.select_one('h3')
        href = article.get('data-href')
        if not href:
            link = article.select_one('a[href*="/s-anzeige/"]')
            href = link.get('href') if link else None
        labels = article.select(POSTING_LABEL_SELECTOR)
        posting_label = labels[0].get_text(' ', strip=True) if len(labels) == 1 else None
        rows.append({'listing_id': article['data-adid'], 'url': urljoin(url, href) if href else None,
                     'title': title.get_text(' ', strip=True) if title else None,
                     'text': text, 'posting_label': posting_label,
                     'posting_label_selector': POSTING_LABEL_SELECTOR, 'posting_label_matches': len(labels),
                     'eligibility': eligibility(text, observed_at, start, end, posting_label=posting_label)})
    following = soup.select_one('a[aria-label="Nächste"]')
    return rows, urljoin(url, following['href']) if following and following.get('href') else None


def search_location_slug(center):
    text = center.casefold().replace('ä', 'ae').replace('ö', 'oe').replace('ü', 'ue').replace('ß', 'ss')
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', '-', text).strip('-')


def search_url_controls(url, manifest):
    """Validate the retained native city/category/radius URL form, not geocode it."""
    parsed = urlparse(url)
    if (parsed.scheme != 'https' or parsed.netloc != 'www.kleinanzeigen.de' or
            parsed.query or parsed.fragment):
        raise ValueError('Use a clean HTTPS Kleinanzeigen search URL without query/fragment or credentials')
    parts = parsed.path.strip('/').split('/')
    match = re.fullmatch(r'c(\d+)l(\d+)r(\d+)', parts[-1])
    if not match or not parts[0].startswith('s-'):
        raise ValueError('Expected a native city/category/radius search URL ending c<CATEGORY>l<LOCATION>r<KM>')
    if int(match[3]) != manifest['radius_km']:
        raise ValueError('Native URL radius does not match frozen radius_km')
    if search_location_slug(manifest['center']) not in parts[1:-1]:
        raise ValueError('Native URL city does not match frozen center; use the native city name in a new configuration')
    return {'native_category_id': int(match[1]), 'native_location_id': int(match[2]),
            'radius_km': int(match[3])}


def prepare_search(run, url, center, radius_km, category, notes):
    """Record a human-inspected native search and bind it to authoritative run state."""
    from pilot.queue import session, event, write, preparation_open
    with session(run) as (run, db, manifest):
        if not manifest.get('configuration') or not manifest.get('sampled_category'):
            raise ValueError('Initialize a new configured run with --category before preparing discovery')
        preparation_open(manifest)
        scope = {'center': center, 'radius_km': radius_km, 'category': category}
        expected = {key: manifest[key] for key in ('center', 'radius_km')}
        expected['category'] = manifest['sampled_category']
        if scope != expected:
            raise ValueError('Observed search scope does not match the frozen run')
        if not isinstance(notes, str) or not notes.strip():
            raise ValueError('Supply --notes describing inspected native category and newest-first ordering')
        controls = search_url_controls(url, manifest)
        existing = db.execute("SELECT 1 FROM events WHERE run=? AND action='search_prepared'", (str(run),)).fetchone()
        if existing or (run / 'search-plan.json').exists() or (run / 'discovery').exists():
            raise ValueError('Search already prepared or collection exists; use a new run to change scope')
        plan = {'schema_version': 1, 'url': url, 'scope': scope, 'native_controls': controls,
                'category_mapping_and_order_notes': notes.strip(),
                'verification': 'operator_inspected_native_controls',
                'listing_scope': 'requires_individual_inspection'}
        write(run / 'search-plan.json', plan)
        event(db, run, None, 'search_prepared', plan)
        return plan


def checked_search(run):
    from pilot.queue import session, read, preparation_open
    with session(run) as (run, db, manifest):
        preparation_open(manifest)
        entry = db.execute("SELECT data FROM events WHERE run=? AND action='search_prepared' ORDER BY id LIMIT 1",
                           (str(run),)).fetchone()
        if not entry:
            raise ValueError('Search is not prepared. Use --prepare-url with inspected controls; see --help')
        plan = json.loads(entry['data'])
        if not (run / 'search-plan.json').is_file() or read(run / 'search-plan.json') != plan:
            raise ValueError('Frozen search plan changed or missing; preserve the run and initialize a new one')
        search_url_controls(plan['url'], manifest)
        return manifest, plan


def main(argv=None, *, operation=None, prog=None):
    parser = argparse.ArgumentParser(prog=prog, description=__doc__, epilog=(
        'First initialize a configured pilot run with --category. Inspect the native city, category, radius '
        'and newest-first order in your browser, then use --prepare-url URL --center CITY --radius-km KM '
        '--category LABEL --notes "Observed category mapping and newest-first order". Preparation is offline. '
        'Then run this command with RUN to collect. Outputs: RUN/discovery/rows.json and summary.json; '
        'import both with pilot import-discovery. Listing category/distance still require individual inspection. '
        'An empty discovery directory can be retried; nonempty evidence is never overwritten.'))
    parser.add_argument('run', type=Path)
    parser.add_argument('--max-pages', type=int, default=20)
    parser.add_argument('--prepare-url', '--url', dest='prepare_url', help='Bind an inspected native search URL to this run, without browsing')
    parser.add_argument('--center', help='Observed native city name, matching the frozen configuration')
    parser.add_argument('--radius-km', type=int)
    parser.add_argument('--category', help='Frozen category label mapped to the inspected native category')
    parser.add_argument('--notes', help='Describe actual category mapping and ordering inspection')
    args = parser.parse_args(argv)
    if operation == 'prepare' and not args.prepare_url:
        parser.error('prepare requires --url and inspected scope controls')
    if operation == 'collect' and args.prepare_url:
        parser.error('Use resale search prepare to configure a search; collect only uses its frozen plan')
    try:
        if args.max_pages < 1:
            raise ValueError('--max-pages must be positive')
        if args.prepare_url:
            prepare_search(args.run, args.prepare_url, args.center, args.radius_km, args.category, args.notes)
            print('Search plan prepared locally. No browser or network used.')
            return 0
        if any(value is not None for value in (args.center, args.radius_km, args.category, args.notes)):
            raise ValueError('Search controls require --prepare-url')
        manifest, seed = checked_search(args.run)
        collect(args.run, manifest, seed, args.max_pages)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        parser.exit(2, f'Error: {exc}\n')
    return 0


def collect(run, manifest, seed, max_pages):
    lock = run / 'discovery.lock'
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise ValueError('Discovery is already running or was interrupted. Remove the empty discovery.lock '
                         'directory only after confirming no collector is running') from exc
    try:
        _collect(run, manifest, seed, max_pages)
    finally:
        lock.rmdir()


def _collect(run, manifest, seed, max_pages):
    start = datetime.fromisoformat(manifest['window_start_inclusive'])
    end = datetime.fromisoformat(manifest['window_end_exclusive'])
    destination = run / 'discovery'
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError('Discovery contains retained files; use a new run. Existing evidence is never overwritten')
    seen, all_rows, visited, pages = set(), [], set(), []
    url, stop = seed['url'], 'page_limit'
    with sync_playwright() as p:
        browser = None
        try:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(locale='de-DE')
            page = context.new_page()
            destination.mkdir(exist_ok=True)
        except Exception as exc:
            if browser is not None:
                browser.close()
            raise RuntimeError('Browser initialization failed; check browser installation and retry the same run: ' + str(exc)) from exc
        try:
            for number in range(1, max_pages + 1):
                if url in visited or urlparse(url).hostname != 'www.kleinanzeigen.de':
                    stop = 'repeated_or_unexpected_next_url'; break
                if search_url_controls(url, manifest) != seed['native_controls']:
                    stop = 'changed_native_search_controls'; break
                visited.add(url)
                response = page.goto(url, wait_until='domcontentloaded', timeout=45000)
                status = response.status if response else None
                if status != 200:
                    stop = f'http_{status}'; break
                if search_url_controls(page.url, manifest) != seed['native_controls']:
                    stop = 'redirect_changed_native_search_controls'; break
                page.locator('article[data-adid]').first.wait_for(timeout=12000)
                observed = datetime.now(timezone.utc)
                html = page.content()
                (destination / f'page-{number:03d}.html.txt').write_text(html, encoding='utf-8')
                rows, next_url = parse_search(html, url, observed, start, end)
                for row in rows:
                    row.update(page=number, observed_at=observed.isoformat())
                    row['duplicate'] = row['listing_id'] in seen
                    seen.add(row['listing_id'])
                    all_rows.append(row)
                pages.append({'page': number, 'url': url, 'observed_at': observed.isoformat(),
                              'rows': len(rows), 'next_url': next_url})
                (destination / 'rows.json').write_text(json.dumps(all_rows, ensure_ascii=False, indent=2), encoding='utf-8')
                print(f'Page {number}: {len(rows)} rows, {len(seen)} unique', flush=True)
                if page_is_before_window(rows, start):
                    stop = 'past_cutoff_page'; break
                if not next_url:
                    stop = 'no_next_link'; break
                url = next_url
                time.sleep(3)
        except Exception as exc:
            stop = f'error: {type(exc).__name__}: {exc}'
        finally:
            browser.close()
    report = {'pages': pages, 'search_plan': seed, 'stop_reason': stop, 'unique_listings': len(seen),
              'row_occurrences': len(all_rows), 'coverage_complete': stop == 'past_cutoff_page',
              'limits': ['Search controls were inspected by an operator; individual listing scope is not certified.',
                         'Live pagination can shift while new listings arrive.',
                         'Search labels may represent renewed visibility; detail posting date must corroborate.',
                         'No claim of marketplace-wide completeness; undated/promoted rows remain unresolved.']}
    (destination / 'rows.json').write_text(json.dumps(all_rows, ensure_ascii=False, indent=2), encoding='utf-8')
    (destination / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k != 'pages'}), flush=True)


def page_is_before_window(rows, start):
    """An undated or overlapping-day row cannot prove the page is past cutoff."""
    ends = [row['eligibility'].get('interval_end_exclusive') for row in rows]
    return bool(ends) and all(end and datetime.fromisoformat(end) <= start for end in ends)


if __name__ == '__main__':
    main()
