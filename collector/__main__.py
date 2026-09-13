"""Run with: python -m collector <listing-url> [--output data]."""

import argparse
from datetime import datetime, timezone
import hashlib
from io import BytesIO
from pathlib import Path
import sys
import time
from uuid import uuid4

from PIL import Image
from .browser import sync_playwright, PlaywrightError

from .extract import listing_url, parse_listing
from .storage import connect, first_seen, save_packet


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def download_photos(context, record, folder):
    for photo in record['photos']:
        photo['download_attempts'] = []
        response = None
        try:
            # Do not follow image redirects to unrelated hosts.
            download_url = photo['source_url']
            response = context.request.get(download_url, timeout=30000, max_redirects=0)
            photo['download_attempts'].append({'url': download_url, 'http_status': response.status})
            if response.status in (404, 410) and photo.get('fallback_url'):
                response.dispose()
                download_url = photo['fallback_url']
                response = context.request.get(download_url, timeout=30000, max_redirects=0)
                photo['download_attempts'].append({'url': download_url, 'http_status': response.status})
                photo['download_note'] = 'Lightbox unavailable; used the same image from the main gallery.'
            if not response.ok:
                raise ValueError(f'Image HTTP {response.status}')
            body = response.body()
            if not response.headers.get('content-type', '').startswith('image/'):
                raise ValueError('Response is not an image')
            with Image.open(BytesIO(body)) as image:
                width, height = image.size
                fmt = image.format
                image.verify()
            extensions = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp', 'AVIF': '.avif', 'GIF': '.gif'}
            if fmt not in extensions:
                raise ValueError(f'Unsupported image format {fmt}')
            filename = f'photos/{photo["index"]:02d}{extensions[fmt]}'
            (folder / filename).write_bytes(body)
            photo.update(status='downloaded', local_path=filename, width=width, height=height,
                         sha256=hashlib.sha256(body).hexdigest(), bytes=len(body), fetched_at=now(),
                         downloaded_url=download_url)
        except (PlaywrightError, OSError, ValueError) as exc:
            photo.update(status='download_error', error=str(exc))
            record['issues'].append(f'Photo {photo["index"]}: {exc}')
        finally:
            if response:
                response.dispose()


def collect(context, db, root, url):
    canonical, identity = listing_url(url)
    started = now()
    capture_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid4().hex[:8]
    folder = root / 'listings' / identity / capture_id
    (folder / 'photos').mkdir(parents=True)
    seen = first_seen(db, identity, canonical, started)
    page = context.new_page()
    record = parse_listing('', canonical, None)
    fetched_at = None
    final_url = None
    try:
        response = page.goto(canonical, wait_until='domcontentloaded', timeout=45000)
        status = response.status if response else None
        final_url = page.url
        if status == 200:
            try:
                page.locator('#viewad-title').wait_for(state='attached', timeout=6000)
            except PlaywrightError:
                pass
        # Short bounded settling time for gallery and seller badges; no infinite network-idle wait.
        page.wait_for_timeout(1000)
        source = page.content()
        fetched_at = now()
        (folder / 'page.html.txt').write_text(source, encoding='utf-8')
        (folder / 'page-visible.txt').write_text(page.locator('body').inner_text(), encoding='utf-8')
        record = parse_listing(source, canonical, status)
        # Independent check against browser-rendered text, not a second HTML parse.
        comparisons = {}
        for name in ('title', 'description', 'asking_price', 'location', 'source_posted_at'):
            item = record['fields'][name]
            locator = page.locator(item['evidence']['selector'])
            if record['page_state'] == 'available' and locator.count() == 1:
                rendered = locator.inner_text()
                matches = ' '.join(rendered.split()) == ' '.join((item['value'] or '').split())
                comparisons[name] = {'matches': matches, 'rendered_text': rendered}
                if not matches:
                    record['issues'].append(f'Extracted {name} differs from rendered browser text')
        record['rendered_text_checks'] = comparisons
        try:
            _, final_id = listing_url(final_url)
            if final_id != identity:
                record['issues'].append('Navigation redirected to another listing')
                record['page_state'] = 'redirected'
        except ValueError:
            record['issues'].append('Navigation left the listing URL')
            record['page_state'] = 'redirected'
        try:
            page.screenshot(path=str(folder / 'page.png'), full_page=True, timeout=15000)
        except PlaywrightError as exc:
            record['issues'].append(f'Screenshot failed: {exc}')
        if record['page_state'] == 'available':
            download_photos(context, record, folder)
    except PlaywrightError as exc:
        record['issues'].append(str(exc))
        record['page_state'] = 'fetch_error'
    finally:
        page.close()
    complete_photos = record['photos'] and all(p['status'] == 'downloaded' for p in record['photos'])
    record.update(capture_id=capture_id, final_url=final_url,
        timestamps={'attempted_at': started, 'first_seen_at': seen, 'details_fetched_at': fetched_at,
                    'photos_fetched_at': now() if complete_photos else None},
        collection_status=('complete' if not record['issues'] else 'partial')
                          if record['page_state'] == 'available' else record['page_state'])
    save_packet(db, folder, record)
    return folder, record


def main(argv=None, *, prog=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(prog=prog, description=__doc__)
    parser.add_argument('urls', nargs='*')
    parser.add_argument('--url-file', type=Path, help='One listing URL per line; # comments allowed')
    parser.add_argument('--output', type=Path, help='Capture directory; defaults to WORKSPACE/captures or ./data')
    parser.add_argument('--workspace', type=Path, help='Private workspace created by resale init')
    parser.add_argument('--delay', type=float, default=3, help='Seconds between listing fetches (minimum 1)')
    args = parser.parse_args(argv)
    urls = args.urls[:]
    if args.url_file:
        urls.extend(line.strip() for line in args.url_file.read_text(encoding='utf-8-sig').splitlines()
                    if line.strip() and not line.lstrip().startswith('#'))
    if not urls:
        parser.error('Supply at least one listing URL or --url-file')
    try:
        urls = list(dict.fromkeys(listing_url(u)[0] for u in urls))
    except ValueError as exc:
        parser.error(str(exc))
    if args.workspace and not (args.workspace / 'resale.toml').is_file():
        parser.error('--workspace must contain resale.toml; create it with resale init')
    root = (args.output or (args.workspace / 'captures' if args.workspace else Path('data'))).resolve()
    db = connect(root)
    failed = False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(locale='de-DE', viewport={'width': 1440, 'height': 1000},
                                          service_workers='block')
            try:
                for index, url in enumerate(urls):
                    if index:
                        time.sleep(max(1, args.delay))
                    folder, record = collect(context, db, root, url)
                    print(f'{record["listing_id"]}: {record["collection_status"]}; '
                          f'{sum(p["status"] == "downloaded" for p in record["photos"])} photos; {folder / "review.html"}', flush=True)
                    for issue in record['issues']:
                        print(f'  {issue}', flush=True)
                    failed |= record['collection_status'] != 'complete'
                    if record['page_state'] == 'blocked':
                        print('Access blocked/rate limited; stopping the batch. No automatic retry.', flush=True)
                        break
            finally:
                context.close()
                browser.close()
    finally:
        db.close()
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
