"""Pure posting-time classification shared by discovery and queue import."""
from datetime import datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo


_NO_POSTING_METADATA = object()


def eligibility(text, observed_at, window_start, window_end, *, posting_label=_NO_POSTING_METADATA):
    """Classify an evidence interval, never infer an exact posting instant.

    New callers supply only the dedicated calendar metadata. An explicitly
    missing/empty label cannot fall back to seller prose. Omitting the keyword
    preserves legacy whole-text minute parsing; date-only prose stays unresolved.
    """
    explicit = posting_label is not _NO_POSTING_METADATA
    if explicit:
        if not isinstance(posting_label, str) or not posting_label.strip():
            return {'status': 'unresolved', 'reason': 'missing_posting_metadata'}
        label = posting_label.strip()
        match = re.fullmatch(r'(Heute|Gestern|\d{2}\.\d{2}\.\d{4})(?:,\s*(\d{2}:\d{2}))?', label)
        if not match:
            return {'status': 'unresolved', 'reason': 'invalid_posting_metadata', 'source_text': label}
    else:
        match = re.search(r'\b(Heute|Gestern|\d{2}\.\d{2}\.\d{4}),\s*(\d{2}:\d{2})\b', text)
        if not match:
            return {'status': 'unresolved', 'reason': 'no_minute_posting_label'}
    local = observed_at.astimezone(ZoneInfo('Europe/Berlin'))
    day, minute = match.groups()
    try:
        posting_day = (local.date() if day == 'Heute' else
                       local.date() - timedelta(days=1) if day == 'Gestern' else
                       datetime.strptime(day, '%d.%m.%Y').date())
        clock = datetime.strptime(minute, '%H:%M').time() if minute else datetime.min.time()
        lo = datetime.combine(posting_day, clock, ZoneInfo('Europe/Berlin')).astimezone(timezone.utc)
        # Add one calendar day before conversion, not 24 hours to a UTC instant.
        hi = (lo + timedelta(minutes=1) if minute else
              datetime.combine(posting_day + timedelta(days=1), datetime.min.time(),
                               ZoneInfo('Europe/Berlin')).astimezone(timezone.utc))
    except ValueError:
        return {'status': 'unresolved', 'reason': 'invalid_posting_date_or_time', 'source_text': match[0]}
    status = ('outside_window' if hi <= window_start or lo >= window_end else
              'eligible' if lo >= window_start and hi <= window_end else 'unresolved')
    precision = 'minute' if minute else 'day'
    return {'status': status, 'source_text': match[0], 'time_precision': precision,
            precision + '_start': lo.isoformat(), precision + '_end_exclusive': hi.isoformat(),
            'interval_end_exclusive': hi.isoformat(),
            'reason': 'boundary_' + precision if status == 'unresolved' else
                      'visible_search_posting_time' if minute else 'visible_search_posting_day'}

