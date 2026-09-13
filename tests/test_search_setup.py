"""Offline browser-boundary experiments for the supervised configured search path."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from collector import discover
from pilot import queue as q
from pilot.config import example_config
from browser_helpers import Browser, Clock, STAMP


class SearchSetupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        clock = patch.object(q, 'now', return_value=STAMP)
        clock.start()
        self.addCleanup(clock.stop)
        sockets = patch('socket.socket', side_effect=AssertionError('No live network in experiment'))
        sockets.start()
        self.addCleanup(sockets.stop)

    def run_profile(self, name='first', center='Hamburg', radius=10, category='computers', hours=6):
        cfg = tomllib.loads(example_config())
        cfg['data_dir'] = str(self.root / (name + '-state'))
        cfg['search'].update(center=center, radius_km=radius, categories=[category], window_hours=hours)
        run = self.root / name
        q.init(run, config=cfg, category=category)
        return run

    def prepare(self, run, url=None):
        m = q.read(run / 'manifest.json')
        url = url or f'https://www.kleinanzeigen.de/s-native-category/{discover.search_location_slug(m["center"])}/c10l20r{m["radius_km"]}'
        args = [str(run), '--prepare-url', url, '--center', m['center'], '--radius-km', str(m['radius_km']),
                '--category', m['sampled_category'], '--notes', 'Synthetic native category mapping; newest-first selected']
        with redirect_stdout(io.StringIO()):
            self.assertEqual(discover.main(args), 0)
        return url

    def replay(self, run, browser=None):
        trace = []
        browser = browser or Browser(trace)
        with patch.object(discover, 'sync_playwright', return_value=browser), \
                patch.object(discover, 'datetime', Clock), redirect_stdout(io.StringIO()):
            self.assertEqual(discover.main([str(run), '--max-pages', '1']), 0)
        return browser.trace

    def test_two_profiles_navigate_their_bound_search_and_window_affects_import(self):
        outcomes = []
        for name, center, radius, category, hours in (
                ('a', 'Hamburg', 10, 'computers', 6), ('b', 'München', 200, 'furniture', 24)):
            run = self.run_profile(name, center, radius, category, hours)
            # Exercise a noncanonical spelling, as Windows TEMP can use short paths.
            run = run / '..' / run.name
            url = self.prepare(run)
            self.assertEqual(self.replay(run), [url])
            q.import_discovery(run, run / 'discovery/rows.json', run / 'discovery/summary.json')
            with q.session(run) as (canonical_run, db, _):
                outcomes.append(dict(db.execute('SELECT listing_id,status FROM sightings WHERE run=?', (str(canonical_run),))))
            self.assertEqual(set(outcomes[-1]), {'901', '902'})
            self.assertEqual(q.select(run, ['901'])['selected'], ['901'])
        self.assertEqual(outcomes[0]['902'], 'outside_window')
        self.assertEqual(outcomes[1]['902'], 'eligible')

    def test_conflicting_city_radius_category_and_legacy_seed_are_rejected(self):
        run = self.run_profile()
        for url, center, radius, category in (
                ('https://www.kleinanzeigen.de/s-native-category/muenchen/c10l20r10', 'Hamburg', 10, 'computers'),
                ('https://www.kleinanzeigen.de/s-native-category/hamburg/c10l20r200', 'Hamburg', 10, 'computers'),
                ('https://www.kleinanzeigen.de/s-native-category/hamburg/c10l20r10', 'Hamburg', 10, 'furniture')):
            with self.assertRaises(ValueError):
                discover.prepare_search(run, url, center, radius, category, 'Synthetic observed controls')
        q.write(run / 'search-browser-check.json', {'url': 'https://www.kleinanzeigen.de/', 'verified': True})
        with patch.object(discover, 'sync_playwright') as browser, redirect_stderr(io.StringIO()) as stderr:
            with self.assertRaises(SystemExit) as error:
                discover.main([str(run)])
            self.assertEqual(error.exception.code, 2)
            self.assertIn('--prepare-url', stderr.getvalue())
            browser.assert_not_called()
        self.assertFalse((run / 'discovery').exists())

    def test_changed_manifest_or_plan_rejected_before_browser_or_output(self):
        for filename in ('manifest.json', 'search-plan.json'):
            run = self.run_profile(filename)
            self.prepare(run)
            original = q.read(run / filename)
            original['unexpected_change'] = True
            q.write(run / filename, original)
            with patch.object(discover, 'sync_playwright') as browser, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    discover.main([str(run)])
                browser.assert_not_called()
            self.assertFalse((run / 'discovery').exists())

    def test_startup_failure_can_retry_without_overwriting_retained_evidence(self):
        run = self.run_profile()
        url = self.prepare(run)
        broken = Browser([])
        with patch.object(broken, 'launch', side_effect=RuntimeError('Synthetic launch failure')), \
                patch.object(discover, 'sync_playwright', return_value=broken), redirect_stderr(io.StringIO()) as stderr:
            with self.assertRaises(SystemExit):
                discover.main([str(run)])
            self.assertIn('retry', stderr.getvalue())
        self.assertFalse((run / 'discovery').exists())
        (run / 'discovery').mkdir()  # Recovery also handles an old, empty failed destination.
        self.assertEqual(self.replay(run), [url])
        original = (run / 'discovery/rows.json').read_bytes()
        with patch.object(discover, 'sync_playwright') as browser, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                discover.main([str(run)])
            browser.assert_not_called()
        self.assertEqual((run / 'discovery/rows.json').read_bytes(), original)

    def test_expired_preparation_is_rejected_before_navigation(self):
        run = self.run_profile()
        self.prepare(run)
        with patch.object(q, 'now', return_value='2026-09-13T13:00:00+00:00'), \
                patch.object(discover, 'sync_playwright') as browser, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                discover.main([str(run)])
            browser.assert_not_called()

    def test_redirect_to_different_controls_does_not_parse_or_retain_listings(self):
        run = self.run_profile()
        self.prepare(run)
        self.replay(run, Browser([], redirect='https://www.kleinanzeigen.de/s-native-category/hamburg/c99l20r10'))
        report = q.read(run / 'discovery/summary.json')
        self.assertEqual(report['stop_reason'], 'redirect_changed_native_search_controls')
        self.assertEqual(report['row_occurrences'], 0)
        self.assertEqual(q.read(run / 'discovery/rows.json'), [])

    def test_concurrent_discovery_cannot_open_browser_or_overwrite_files(self):
        run = self.run_profile()
        self.prepare(run)
        (run / 'discovery.lock').mkdir()
        with patch.object(discover, 'sync_playwright') as browser, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                discover.main([str(run)])
            browser.assert_not_called()
        self.assertFalse((run / 'discovery').exists())
