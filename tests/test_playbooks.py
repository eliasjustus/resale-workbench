"""One canonical editable source; each run retains an immutable copy."""
from importlib import resources
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pilot import queue


class PlaybookTests(unittest.TestCase):
    def test_source_checkout_resolves_canonical_resources_and_old_run_stays_frozen(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(queue, 'now', return_value='2026-09-13T12:00:00+00:00'):
            root = Path(temp)
            source = root / 'source'
            canonical = source / 'resale_tool/resources'
            canonical.mkdir(parents=True)
            for role in ('REVIEWER.md', 'VALUATOR.md'):
                (source / role).write_text('Navigation only', encoding='utf-8')
                (canonical / role).write_text('Synthetic canonical instructions ' + role, encoding='utf-8')
            state = root / 'state.sqlite3'
            first = root / 'first'
            queue.init(first, state=state, playbook_root=source)
            before = (first / 'playbooks/REVIEWER.md').read_bytes()
            self.assertEqual(before, (canonical / 'REVIEWER.md').read_bytes())
            (canonical / 'REVIEWER.md').write_text('New synthetic revision', encoding='utf-8')
            second = root / 'second'
            queue.init(second, state=state, playbook_root=source)
            self.assertEqual((second / 'playbooks/REVIEWER.md').read_bytes(), (canonical / 'REVIEWER.md').read_bytes())
            self.assertEqual((first / 'playbooks/REVIEWER.md').read_bytes(), before)
            with queue.session(first, read_only=True):
                pass

    def test_default_run_uses_exact_packaged_instructions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            queue.init(root / 'run', state=root / 'state.sqlite3')
            for role in ('REVIEWER.md', 'VALUATOR.md'):
                self.assertEqual((root / 'run/playbooks' / role).read_bytes(),
                                 resources.files('resale_tool').joinpath('resources', role).read_bytes())
