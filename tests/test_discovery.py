from datetime import datetime
import unittest
from collector.discover import eligibility, parse_search, page_is_before_window


class DiscoveryTests(unittest.TestCase):
    def test_fixed_window_and_minute_ambiguity(self):
        start = datetime.fromisoformat('2026-09-11T12:16:25Z')
        end = datetime.fromisoformat('2026-09-12T12:16:25Z')
        observed = datetime.fromisoformat('2026-09-12T12:30:00Z')
        for label, expected in [('Heute, 14:15','eligible'), ('Heute, 14:16','unresolved'),
                                ('Heute, 14:17','outside_window'), ('Gestern, 14:15','outside_window'),
                                ('Gestern, 14:16','unresolved'), ('Gestern, 14:17','eligible'),
                                ('11.09.2026','unresolved')]:
            with self.subTest(label=label):
                self.assertEqual(eligibility(label, observed, start, end)['status'], expected)

    def test_relative_labels_use_observation_day_after_midnight(self):
        result = eligibility('Gestern, 23:30', datetime.fromisoformat('2026-09-12T22:05:00Z'),
                             datetime.fromisoformat('2026-09-12T00:00:00Z'),
                             datetime.fromisoformat('2026-09-12T22:00:00Z'))
        self.assertEqual(result['minute_start'], '2026-09-12T21:30:00+00:00')

    def classify(self, label, start='2026-09-11T16:54:24Z', end='2026-09-12T16:54:24Z'):
        return eligibility('Title contains Heute, 18:00', datetime.fromisoformat('2026-09-12T17:00:00Z'),
                           datetime.fromisoformat(start), datetime.fromisoformat(end), posting_label=label)

    def test_old_posting_day_is_outside_without_inventing_minute(self):
        result = self.classify('10.09.2026')
        self.assertEqual(result['status'], 'outside_window')
        self.assertEqual(result['time_precision'], 'day')
        self.assertEqual(result['day_start'], '2026-09-09T22:00:00+00:00')
        self.assertEqual(result['day_end_exclusive'], '2026-09-10T22:00:00+00:00')
        self.assertNotIn('minute_start', result)

    def test_boundary_day_is_unresolved_and_disjoint_future_day_outside(self):
        for label in ('11.09.2026', '12.09.2026'):
            with self.subTest(label=label):
                result = self.classify(label)
                self.assertEqual(result['status'], 'unresolved')
                self.assertEqual(result['reason'], 'boundary_day')
        self.assertEqual(self.classify('13.09.2026')['status'], 'outside_window')

    def test_explicit_missing_or_invalid_metadata_never_uses_title_minute(self):
        for label in (None, '', '  ', '31.02.2026', '10.09.2026, 25:61', 'published 10.09.2026'):
            with self.subTest(label=label):
                result = self.classify(label)
                self.assertEqual(result['status'], 'unresolved')
                self.assertNotIn('minute_start', result)

    def test_calendar_day_bounds_follow_berlin_dst(self):
        for label, start, end, hours in (
                ('29.03.2026', '2026-03-28T23:00:00+00:00', '2026-03-29T22:00:00+00:00', 23),
                ('25.10.2026', '2026-10-24T22:00:00+00:00', '2026-10-25T23:00:00+00:00', 25)):
            with self.subTest(label=label):
                result = self.classify(label)
                self.assertEqual(result['day_start'], start)
                self.assertEqual(result['day_end_exclusive'], end)
                self.assertEqual((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds(), hours * 3600)

    def test_fully_contained_day_interval_can_be_eligible(self):
        result = self.classify('29.03.2026', '2026-03-28T23:00:00Z', '2026-03-29T23:00:00Z')
        self.assertEqual(result['status'], 'eligible')
        self.assertEqual(result['time_precision'], 'day')

    def test_parser_uses_only_calendar_metadata_not_title_or_description(self):
        html = '''<article data-adid="111" data-href="/s-anzeige/example/111-1-1">
          <h3>Heute, 18:00: model released 10.09.2026</h3><p>Gestern, 18:50 fake posting date</p>
          <div><svg data-title="calendarOutline"></svg><span>10.09.2026</span></div></article>
          <article data-adid="222"><h3>Heute, 18:00</h3><p>10.09.2026</p></article>
          <article data-adid="333"><h3>Heute, 18:00</h3>
          <div><svg data-title="calendarOutline"></svg><span></span></div></article>'''
        rows, _ = parse_search(html, 'https://www.kleinanzeigen.de/',
                              datetime.fromisoformat('2026-09-12T17:00:00Z'),
                              datetime.fromisoformat('2026-09-11T16:54:24Z'),
                              datetime.fromisoformat('2026-09-12T16:54:24Z'))
        self.assertEqual(rows[0]['posting_label'], '10.09.2026')
        self.assertEqual(rows[0]['eligibility']['status'], 'outside_window')
        self.assertIsNone(rows[1]['posting_label'])
        self.assertEqual(rows[2]['posting_label'], '')
        for row in rows[1:]:
            self.assertEqual(row['eligibility']['reason'], 'missing_posting_metadata')

    def test_legacy_date_only_prose_remains_unresolved(self):
        result = eligibility('Released on 10.09.2026', datetime.fromisoformat('2026-09-12T17:00:00Z'),
                             datetime.fromisoformat('2026-09-11T16:54:24Z'),
                             datetime.fromisoformat('2026-09-12T16:54:24Z'))
        self.assertEqual(result['reason'], 'no_minute_posting_label')

    def test_page_cutoff_requires_every_row_interval_proven_old(self):
        start = datetime.fromisoformat('2026-09-11T16:54:24Z')
        old_day = {'eligibility': self.classify('10.09.2026')}
        old_minute = {'eligibility': self.classify('11.09.2026, 18:00')}
        self.assertTrue(page_is_before_window([old_day, old_minute], start))
        for unknown in (self.classify(None), self.classify('11.09.2026')):
            self.assertFalse(page_is_before_window([old_minute, {'eligibility': unknown}], start))
