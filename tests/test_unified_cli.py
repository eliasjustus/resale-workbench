"""Exercise the installed user's command journey with synthetic browser responses."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from collector import discover
from pilot import queue as q
from resale_tool.cli import main
from browser_helpers import Browser, Clock, STAMP


class UnifiedCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        for mock in (patch.object(q, 'now', return_value=STAMP),
                     patch('socket.socket', side_effect=AssertionError('No live network'))):
            mock.start()
            self.addCleanup(mock.stop)

    def command(self, *args):
        with redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
            code = main([str(arg) for arg in args])
        self.assertEqual(code, 0, output.getvalue())
        return output.getvalue()

    def workspace(self):
        workspace = self.root / 'private workspace'
        self.command('init', workspace)
        config = workspace / 'resale.toml'
        config.write_text(config.read_text().replace('Berlin', 'Hamburg').replace('radius_km = 50', 'radius_km = 10'), encoding='utf-8')
        result = json.loads(self.command('run', 'init', '--config', config))
        run = Path(result['run_path'])
        self.assertEqual(run.parent, workspace / 'runs')
        self.assertEqual(result['sampled_category'], 'computers')
        return workspace, run

    def test_one_interface_from_workspace_to_review_prerequisites(self):
        workspace, run = self.workspace()
        before = q.tree(workspace)
        status = json.loads(self.command('run', 'status', run))
        self.assertEqual(q.tree(workspace), before, 'Status must not write reports or change coordination state')
        self.assertEqual(status['search'], 'not_prepared')
        self.assertFalse(status['jobs_started'])
        self.command('search', 'prepare', run, '--url', 'https://www.kleinanzeigen.de/s-native-category/hamburg/c10l20r10',
                     '--center', 'Hamburg', '--radius-km', '10', '--category', 'computers',
                     '--notes', 'Synthetic category mapping and newest-first inspection')
        self.assertEqual(json.loads(self.command('run', 'status', run))['search'], 'prepared')
        browser = Browser([])
        with patch.object(discover, 'sync_playwright', return_value=browser), patch.object(discover, 'datetime', Clock):
            self.command('search', 'collect', run, '--max-pages', '1')
        status = json.loads(self.command('run', 'status', run))
        self.assertFalse(status['discovery']['coverage_complete'])
        self.assertIn('import-discovery', status['next_action'])
        self.command('run', 'import-discovery', run)
        self.command('run', 'select', run, '901')
        status = json.loads(self.command('run', 'status', run))
        self.assertEqual(status['discovery_imports'], 1)
        self.assertEqual(status['cases'][0]['stage'], 'selected')
        self.assertIn('privacy', status['cases'][0]['next_action'])
        with redirect_stdout(io.StringIO()) as output:
            code = main(['run', 'job', str(run), '901', 'reviewer'])
        self.assertEqual(code, 1)
        self.assertIn('cannot prepare from stage selected', output.getvalue())
        self.assertFalse((run / 'jobs').exists())

    def test_search_subcommands_do_not_accidentally_collect_or_prepare(self):
        _, run = self.workspace()
        with patch.object(discover, 'sync_playwright') as browser:
            for args in (['search', 'prepare', str(run)],
                         ['search', 'collect', str(run), '--url', 'https://www.kleinanzeigen.de/']):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    main(args)
            browser.assert_not_called()
        self.assertFalse((run / 'search-plan.json').exists())

    def test_status_flags_changed_search_plan_and_expired_preparation(self):
        _, run = self.workspace()
        self.command('search', 'prepare', run, '--url', 'https://www.kleinanzeigen.de/s-native-category/hamburg/c10l20r10',
                     '--center', 'Hamburg', '--radius-km', '10', '--category', 'computers', '--notes', 'Synthetic controls')
        plan = q.read(run / 'search-plan.json')
        plan['url'] += '/changed'
        q.write(run / 'search-plan.json', plan)
        status = json.loads(self.command('run', 'status', run))
        self.assertEqual(status['search'], 'invalid')
        self.assertTrue(status['integrity_errors'])
        with patch.object(q, 'now', return_value='2026-09-13T13:00:00+00:00'):
            self.assertFalse(json.loads(self.command('run', 'status', run))['preparation_open'])

    def test_capture_uses_private_workspace_and_explicit_output_override(self):
        from collector import __main__ as capture
        workspace, _ = self.workspace()
        for extra, expected in (([], workspace / 'captures'),
                                (['--output', str(self.root / 'explicit')], self.root / 'explicit')):
            with patch.object(capture, 'sync_playwright', return_value=Browser([])), \
                    patch.object(capture, 'collect', return_value=(expected / 'fixture', {
                        'listing_id': '111', 'collection_status': 'complete', 'photos': [], 'issues': [],
                        'page_state': 'available'})) as collect:
                self.command('capture', 'https://www.kleinanzeigen.de/s-anzeige/synthetic/111-1-1',
                             '--workspace', workspace, *extra)
                self.assertEqual(collect.call_args.args[2], expected)

    def test_help_for_each_group_is_offline(self):
        for args in (['--help'], ['run', '--help'], ['search', '--help'], ['search', 'prepare', '--help'],
                     ['search', 'collect', '--help'], ['capture', '--help']):
            with redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit) as error:
                main(args)
            self.assertEqual(error.exception.code, 0)
            self.assertIn('usage:', output.getvalue())

    def test_status_never_creates_missing_database_or_initializes_empty_schema(self):
        _, run = self.workspace()
        missing = self.root / 'missing-state-parent' / 'state.sqlite3'
        q.write(run / 'pilot.json', {'state': str(missing)})
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(['run', 'status', str(run)]), 1)
        self.assertFalse(missing.parent.exists())
        empty = self.root / 'empty.sqlite3'
        sqlite3.connect(empty).close()
        q.write(run / 'pilot.json', {'state': str(empty)})
        before = empty.read_bytes()
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(['run', 'status', str(run)]), 1)
        self.assertEqual(empty.read_bytes(), before)
