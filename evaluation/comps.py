"""Normalize saved, rendered eBay Product Research rows; no browser/network access."""

import re
from datetime import date
from urllib.parse import parse_qs, urlsplit


def normalize_research(snapshot, source_path):
    query = parse_qs(urlsplit(snapshot['url']).query)
    if query.get('tabName') != ['SOLD']:
        raise ValueError('Snapshot is not the sold-results tab')
    months = dict(zip(('Jan', 'Feb', 'Mrz', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez'), range(1, 13)))
    result = []
    for row_index, row in enumerate(snapshot['rows'][1:], 1):
        cells = row['cells']
        if len(cells) != 8:
            raise ValueError('Product Research table layout changed')
        prices = []
        for cell in (cells[2], cells[3]):
            match = re.fullmatch(r'([\d.]+,\d{2})\s*€', cell.splitlines()[0])
            if not match:
                raise ValueError('Unrecognized displayed EUR price')
            prices.append(match[1].replace('.', '').replace(',', '.'))
        stamp = re.fullmatch(r'(\d{1,2})\. (\w+) (\d{4})', cells[7])
        if not stamp or stamp[2] not in months:
            raise ValueError('Unrecognized sold date')
        url = next((link['url'] for link in row['links'] if '/itm/' in link['url']), None)
        identity = re.search(r'/itm/(\d+)', url or '')
        result.append({'sale_id': 'ebay:' + identity[1] if identity else None,
                       'source_url': url, 'source_snapshot': source_path, 'source_row': row_index,
                       'source_captured_at': snapshot['captured_at'],
                       'title': cells[0].splitlines()[-1], 'currency': 'EUR',
                       'item_price_eur': prices[0], 'shipping_eur': prices[1],
                       'price_basis': 'actual_sold_item_price' if cells[4] == '1' else 'aggregate_average',
                       'units_sold': int(cells[4]),
                       'sold_at': date(int(stamp[3]), months[stamp[2]], int(stamp[1])).isoformat(),
                       'seller_country': 'DE' if query.get('sellerCountry') == ['SellerLocation:::DE'] else None,
                       'source_condition_filter': query.get('conditionId', [None])[0],
                       'review_status': 'pending', 'review_evidence': [],
                       'review_reason': 'Check exact variant, actual-item condition, included accessories and geography; a search hit is not an audited comparable.'})
    return result
