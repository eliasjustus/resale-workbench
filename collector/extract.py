"""Parse a rendered Kleinanzeigen detail page without trusting listing instructions."""

import copy
import re
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup


def listing_url(value):
    parts = urlsplit(value.strip())
    if (parts.scheme != 'https' or parts.hostname not in
            {'www.kleinanzeigen.de', 'kleinanzeigen.de'} or parts.username or
            parts.password or parts.port not in (None, 443)):
        raise ValueError('Expected an HTTPS Kleinanzeigen listing URL')
    match = re.fullmatch(r'/s-anzeige/[^/]+/(\d+)-\d+-\d+/?', parts.path)
    if not match:
        raise ValueError('Expected /s-anzeige/<title>/<id>-<category>-<location>')
    return urlunsplit(('https', 'www.kleinanzeigen.de', parts.path.rstrip('/'), '', '')), match[1]


def visible_text(element, multiline=False):
    if element is None:
        return None
    element = copy.deepcopy(element)
    for hidden in element.select('script, style, [hidden], [aria-hidden="true"], .is-hidden, .mfp-hide'):
        hidden.decompose()
    for node in list(element.select('[style]')):
        if re.search(r'(display\s*:\s*none|visibility\s*:\s*hidden)', node.get('style', ''), re.I):
            node.decompose()
    for br in element.select('br'):
        br.replace_with('\n')
    raw = element.get_text('' if multiline else ' ').replace('\r\n', '\n').strip()
    return raw if multiline else ' '.join(raw.split())


def field(value, selector, *, absent='extraction_error', note=None):
    return {'value': value if value not in ('', None) else None,
            'status': 'observed' if value not in ('', None) else absent,
            'evidence': {'file': 'page.html.txt', 'selector': selector}, 'note': note}


def parse_listing(html, url, http_status=200):
    canonical, identity = listing_url(url)
    soup = BeautifulSoup(html, 'html.parser')
    issues = []
    title = soup.select_one('#viewad-title')
    # Never scan seller-authored descriptions for page-error or access-control messages.
    if http_status in (403, 429):
        state = 'blocked'
    elif http_status in (404, 410):
        state = 'unavailable'
    elif http_status is None or http_status >= 400:
        state = 'fetch_error'
    elif title is None:
        state = 'unrecognized_page'
    else:
        state = 'available'

    def read(selector, **kwargs):
        return field(visible_text(soup.select_one(selector)), selector, **kwargs)

    fields = {
        'title': read('#viewad-title'),
        'description': field(visible_text(soup.select_one('#viewad-description-text'), True),
                             '#viewad-description-text'),
        'asking_price': read('#viewad-price'),
        'location': read('#viewad-locality'),
        'source_posted_at': read('#viewad-extra-info > div:first-child > span',
                               note='Source text retained; no time or timezone inferred.'),
        'shipping': read('.boxedarticle--details--shipping', absent='source_unavailable'),
    }
    if fields['source_posted_at']['value'] is None:
        fields['source_posted_at'] = read('#viewad-main-info svg[data-title="calendarOutline"] + span',
                                        note='Source text retained; no time or timezone inferred.')
    seller = soup.select_one('#viewad-contact')
    seller_details = [visible_text(n) for n in soup.select('#viewad-contact .userprofile-vip-details-text')]
    since = next((v for v in seller_details if v.startswith('Aktiv seit ')), None)
    kind = next((v for v in seller_details if 'Nutzer' in v or 'Anbieter' in v), None)
    absent = 'source_unavailable' if seller else 'extraction_error'
    fields['seller_active_since'] = field(since, '#viewad-contact .userprofile-vip-details-text', absent=absent)
    fields['seller_type'] = field(kind, '#viewad-contact .userprofile-vip-details-text', absent=absent)
    badges = [visible_text(n) for n in soup.select('#viewad-contact .userbadge-tag')]
    fields['seller_badges'] = field(badges or None, '#viewad-contact .userbadge-tag', absent=absent,
                                    note='Visible labels only; absence is not a negative rating.')
    details = soup.select_one('#viewad-details')
    fields['attributes'] = field(visible_text(details), '#viewad-details',
                                 absent='source_unavailable',
                                 note='Listing attributes are seller claims, not verified condition.')

    id_node = soup.select_one('#viewad-ad-id-box li:last-child')
    id_selector = '#viewad-ad-id-box li:last-child'
    if id_node is None:
        id_selector = '#viewad-ad-id-box > div > span:last-child'
        id_node = soup.select_one(id_selector)
    displayed_id = visible_text(id_node)
    fields['displayed_listing_id'] = field(displayed_id, id_selector)
    if state == 'available' and displayed_id != identity:
        issues.append('Displayed listing ID missing or differs from requested ID')
    canonical_node = soup.select_one('link[rel="canonical"]')
    if canonical_node:
        try:
            candidate, candidate_id = listing_url(canonical_node.get('href', ''))
            if candidate_id != identity:
                issues.append('Canonical URL points to another listing')
            else:
                canonical = candidate
        except ValueError:
            issues.append('Unrecognized canonical URL')

    # Lightbox data-imgsrc points at the larger image exposed by the page.
    # Scope to this listing: recommendations elsewhere also contain images/JSON-LD.
    thumbs = soup.select('#viewad-lightbox-thumbnail-list img')
    gallery = soup.select('.vip-image-gallery .galleryimage-element[data-ix] img')
    gallery_selector = ('#viewad-lightbox-thumbnail-list img' if thumbs else
                        '.vip-image-gallery .galleryimage-element[data-ix] img')
    modern_gallery = None
    if not thumbs and not gallery:
        primary = soup.select_one('img#viewad-image')
        modern_gallery = primary.find_parent('astro-island') if primary else None
        if modern_gallery:
            gallery = modern_gallery.select('[data-ix][aria-label^="Bild "][role="button"] > img')
            gallery_selector = 'astro-island:has(img#viewad-image) [data-ix][aria-label^="Bild "][role="button"] > img'
            indices = [int(n.parent['data-ix']) for n in gallery if str(n.parent.get('data-ix', '')).isdigit()]
            if sorted(indices) != list(range(len(gallery))):
                issues.append('Modern gallery positions are missing, repeated or invalid')
            else:
                gallery = sorted(gallery, key=lambda n: int(n.parent['data-ix']))
    photos, seen = [], set()
    main_sources = {}
    for node in gallery:
        source = node.get('data-imgsrc') or node.get('src')
        if source:
            main_sources[urlsplit(source).path] = source
    for position, node in enumerate(thumbs or gallery, 1):
        source = node.get('data-imgsrc') or node.get('src')
        parsed = urlsplit(source or '')
        if (parsed.scheme != 'https' or parsed.hostname != 'img.kleinanzeigen.de'
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or not parsed.path.startswith('/api/v1/prod-ads/images/')):
            issues.append(f'Gallery position {position} has no supported image URL')
            continue
        key = parsed.path
        if key in seen:
            issues.append(f'Duplicate image at gallery position {position}')
            continue
        seen.add(key)
        fallback = main_sources.get(key)
        if fallback:
            fallback_parts = urlsplit(fallback)
            if (fallback_parts.scheme != 'https' or fallback_parts.hostname != 'img.kleinanzeigen.de'
                    or fallback_parts.username or fallback_parts.password or fallback_parts.port not in (None, 443)):
                fallback = None
        photos.append({'index': position, 'source_url': source,
                       'fallback_url': fallback if fallback != source else None, 'status': 'pending',
                       'evidence': {'file': 'page.html.txt', 'selector': gallery_selector,
                                    'position': position}})

    counter_text = visible_text(soup.select_one('.vip-image-gallery .j-gallery-info'))
    if modern_gallery:
        match = re.search(r'\b\d+\s*/\s*\d+\b', modern_gallery.get_text(' ', strip=True))
        counter_text = match[0] if match else None
    count_match = re.search(r'/\s*(\d+)', counter_text or '')
    expected = int(count_match[1]) if count_match else None
    if expected is not None and len(photos) != expected:
        issues.append(f'Gallery counter says {expected}; extracted {len(photos)} images')
    if thumbs and gallery and len(thumbs) != len(gallery):
        issues.append('Lightbox and main gallery image counts disagree')
    if state == 'available' and not photos:
        issues.append('No gallery images extracted; needs source inspection')
    if state == 'available':
        for name, item in fields.items():
            if item['status'] == 'extraction_error':
                issues.append(f'Missing required field: {name}')
    else:
        for item in fields.values():
            item['value'], item['status'] = None, 'not_collected'
        photos = []
        issues.append(f'Listing could not be collected: {state}')

    return {'schema_version': 1, 'listing_id': identity, 'canonical_url': canonical,
            'page_state': state, 'http_status': http_status, 'fields': fields,
            'photos': photos, 'gallery': {'visible_counter': counter_text,
                'expected_count': expected, 'lightbox_count': len(thumbs),
                'main_gallery_count': len(gallery),
                'verification': 'not_manually_verified'},
            'issues': issues, 'evaluation': {'status': 'not_evaluated', 'outcome': None}}
