"""Offline entry points for setup, review and the fictional demonstration."""

import argparse
from importlib import metadata, resources, util
import json
import os
from pathlib import Path
import sys
import tempfile
from zoneinfo import ZoneInfo

from . import __version__
from .demo import create_demo, write_json
from evaluation.service import resolve_policy, draft_review, evaluate_capture


def storage_readiness(data_dir):
    """Probe local writes without retaining files or removing pre-existing paths."""
    target = Path(data_dir)
    created = []
    result = {'checked': True, 'usable': False,
              'probe': 'Create, write, read and remove a temporary file in the selected data directory.',
              'limits': 'Checks current filesystem access; not database validity, free capacity or future permissions.'}
    try:
        missing = []
        current = target
        while not current.exists():
            missing.append(current)
            if current.parent == current:
                raise OSError('No accessible existing ancestor for data_dir')
            current = current.parent
        if not current.is_dir():
            raise NotADirectoryError('data_dir or an existing ancestor is a file, not a directory')
        for folder in reversed(missing):
            try:
                folder.mkdir()
                created.append(folder)
            except FileExistsError:
                # Another process may have created the directory after the check.
                if not folder.is_dir():
                    raise
        with tempfile.TemporaryFile(mode='w+b', prefix='.resale-doctor-', dir=target) as probe:
            probe.write(b'resale local storage probe\n')
            probe.flush()
            os.fsync(probe.fileno())
            probe.seek(0)
            if probe.read() != b'resale local storage probe\n':
                raise OSError('Temporary storage read did not match written bytes')
        result['usable'] = True
    except OSError as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        for folder in reversed(created):
            try:
                folder.rmdir()
            except OSError as exc:
                result['usable'] = False
                result.setdefault('cleanup_errors', []).append(f'{type(exc).__name__}: {exc}')
    return result


def doctor(config_path=None):
    checks = {'python_supported': sys.version_info >= (3, 11)}
    versions = {}
    for distribution in ('beautifulsoup4', 'Pillow'):
        try:
            versions[distribution] = metadata.version(distribution)
            checks[distribution] = True
        except metadata.PackageNotFoundError:
            checks[distribution] = False
    for name in ('REVIEWER.md', 'VALUATOR.md'):
        checks[name] = resources.files('resale_tool').joinpath('resources', name).is_file()
    try:
        ZoneInfo('Europe/Berlin')
        checks['timezone_data'] = True
    except KeyError:
        checks['timezone_data'] = False
    from pilot.config import default_data_dir, load_config
    if config_path:
        configuration = load_config(config_path)
        checks['configuration'] = True
        data_dir = configuration['data_dir']
    else:
        data_dir = default_data_dir()
    storage = storage_readiness(data_dir)
    checks['storage_writable'] = storage['usable']
    result = {'version': __version__, 'checks': checks, 'dependency_versions': versions,
              'offline_ready': all(checks.values()),
              'storage': storage,
              'configuration_checked': bool(config_path),
              'model_identifier_syntax_checked': bool(config_path),
              'model_availability_checked': False,
              'model_effort_compatibility_checked': False,
              'source_access_checked': False,
              'optional_browser_package_installed': util.find_spec('playwright') is not None,
              'browser_binaries_checked': False, 'network_used': False,
              'readiness_scope': 'Local dependencies, packaged instructions and temporary storage writes; '
                  'configuration syntax when supplied. Does not establish browser, source or external model availability.',
              'agent_executor': 'manual; no automatic agent dispatch configured'}
    print(json.dumps(result, indent=2))
    return 0 if result['offline_ready'] else 1


def init_workspace(destination):
    from pilot.config import example_config
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    config_text = example_config().replace('# data_dir = "./private-data"', 'data_dir = "./private-data"')
    (destination / 'resale.toml').write_text(config_text, encoding='utf-8')
    (destination / '.gitignore').write_text('*\n', encoding='utf-8')
    (destination / 'README.txt').write_text(
        'Private local resale workspace.\n'
        'Edit resale.toml before starting a run. Choose your location and optional role settings.\n'
        'Check it with: resale doctor --config resale.toml\n'
        'Run these commands from this workspace, or pass the full config path.\n'
        'data_dir resolves beside resale.toml; relative RUN/output paths resolve from your current directory.\n'
        'Keep run folders inside this private workspace, for example ./runs/first-review.\n'
        'Run an offline demonstration separately with: resale demo --output demo\n'
        'Start: resale run init --config resale.toml (one category is inferred; otherwise pass --category)\n'
        'Next: resale run status RUN; resale search prepare --help; resale capture --help\n'
        'Command help: resale --help\n'
        'Full guides in the source candidate: README.md (setup/manual review), RUNNER.md (coordinator workflow), '
        'PRIVACY.md (evidence handling). Wheel-only installs do not include these source guides.\n'
        'Doctor checks local storage and syntax; it does not verify model availability or source access.\n'
        'No network access, scheduling, agent dispatch or purchases were started.\n', encoding='utf-8')
    return destination / 'resale.toml'


def search_main(argv):
    from collector.discover import main as discover_main
    if argv and argv[0] in ('prepare', 'collect'):
        return discover_main(argv[1:], operation=argv[0], prog='resale search ' + argv[0])
    parser = argparse.ArgumentParser(prog='resale search', description='Supervised native search setup and explicit collection.')
    commands = parser.add_subparsers(dest='operation', required=True)
    commands.add_parser('prepare', help='Bind inspected source controls to a run; offline')
    commands.add_parser('collect', help='Use the frozen search plan; requires authorized live access')
    parser.parse_args(argv)
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == 'run':
        from pilot.queue import main as run_main
        return run_main(argv[1:], prog='resale run')
    if argv and argv[0] == 'capture':
        from collector.__main__ import main as capture_main
        return capture_main(argv[1:], prog='resale capture')
    if argv and argv[0] == 'search':
        return search_main(argv[1:])
    parser = argparse.ArgumentParser(description='Resale Workbench: local evidence review. Default commands are offline.')
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('run', help='Initialize, inspect and coordinate a private run', add_help=False)
    commands.add_parser('search', help='Prepare an inspected search or explicitly collect it', add_help=False)
    commands.add_parser('capture', help='Explicitly collect listing evidence into a private workspace', add_help=False)
    check = commands.add_parser('doctor', help='Check local dependencies and optional configuration; no network')
    check.add_argument('--config', type=Path)
    init = commands.add_parser('init', help='Create a new private workspace with editable configuration')
    init.add_argument('workspace', type=Path)
    demo = commands.add_parser('demo', help='Create three fictional examples and an HTML review, entirely offline')
    demo.add_argument('--output', required=True, type=Path, help='New directory; existing files are never overwritten')
    draft = commands.add_parser('draft-review', help='Create an unresolved manual review bound to a retained capture')
    draft.add_argument('capture', type=Path, help='Directory containing record.json')
    draft.add_argument('--output', required=True, type=Path, help='New JSON file to edit manually')
    draft_policy = draft.add_mutually_exclusive_group()
    draft_policy.add_argument('--config', type=Path, help='Freeze configured economic policy in the draft')
    draft_policy.add_argument('--run', type=Path, help='Freeze policy from an integrity-checked run')
    gate = commands.add_parser('evaluate', help='Check a completed manual review against its immutable source record')
    gate.add_argument('capture', type=Path)
    gate.add_argument('review', type=Path)
    gate.add_argument('--output', required=True, type=Path, help='New JSON result file')
    gate.add_argument('--legacy-replay', action='store_true', help='Replay schema-1 historical declarations; not current evidence verification')
    gate_policy = gate.add_mutually_exclusive_group()
    gate_policy.add_argument('--config', type=Path, help='Economic policy; must match any policy frozen in review')
    gate_policy.add_argument('--run', type=Path, help='Use an integrity-checked run policy')
    seal = commands.add_parser('seal-review', help='Bind referenced retained files after completing a manual review')
    seal.add_argument('capture', type=Path)
    seal.add_argument('review', type=Path)
    seal.add_argument('--evidence-root', type=Path, help='Root for comparable source_paths; defaults beside review JSON')
    seal.add_argument('--output', type=Path, required=True, help='New review JSON with retained file hashes')
    workspace = commands.add_parser('privacy-workspace', help='Seed private editable text/photos from a complete capture')
    workspace.add_argument('capture', type=Path)
    workspace.add_argument('destination', type=Path)
    template = commands.add_parser('privacy-template', help='Freeze current sanitized bytes into a pending human-review template')
    template.add_argument('capture', type=Path)
    template.add_argument('sanitized', type=Path)
    template.add_argument('--output', type=Path, required=True)
    private = commands.add_parser('prepare-private', help='Export a manually reviewed model-input derivative')
    private.add_argument('capture', type=Path)
    private.add_argument('destination', type=Path)
    private.add_argument('--sanitized', type=Path, required=True)
    private.add_argument('--review', type=Path, required=True)
    inspect = commands.add_parser('check-privacy', help='Verify reviewed derivative hashes and original linkage')
    inspect.add_argument('capture', type=Path)
    inspect.add_argument('packet', type=Path)
    support = commands.add_parser('support-summary', help='Export only fixed fields and aggregate counts from a local run')
    support.add_argument('run', type=Path)
    support.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'doctor':
            return doctor(args.config)
        if args.command == 'init':
            print(init_workspace(args.workspace))
        elif args.command == 'demo':
            print(create_demo(args.output))
        elif args.command == 'draft-review':
            write_json(args.output, draft_review(args.capture, args.config, args.run))
            print(f'Unresolved draft created: {args.output.resolve()}')
        elif args.command == 'evaluate':
            result = evaluate_capture(args.capture, args.review, args.config, args.run, legacy_replay=args.legacy_replay)
            write_json(args.output, result)
            prefix = "Historical replay only: " if args.legacy_replay else ""
            print(f"{prefix}{result['outcome']}: {', '.join(result['reasons'])}")
        elif args.command == 'privacy-workspace':
            from evaluation.privacy import prepare_privacy_workspace
            print(json.dumps(prepare_privacy_workspace(args.capture, args.destination), indent=2))
        elif args.command == 'seal-review':
            from evaluation.manual import seal_review
            write_json(args.output, seal_review(args.capture, args.review, args.evidence_root))
            print('Retained files bound. Hashes do not certify source facts, inspection or comparable fit.')
        elif args.command == 'privacy-template':
            from evaluation.privacy import create_review_template
            write_json(args.output, create_review_template(args.capture, args.sanitized))
            print('Pending review created. Inspect the text and every photo before filling declarations.')
        elif args.command == 'prepare-private':
            from evaluation.privacy import export_reviewed_packet
            export_reviewed_packet(args.capture, args.destination, args.sanitized, args.review)
            print('Reviewed derivative exported. Price blinding remains a separate check; publication is not authorized.')
        elif args.command == 'check-privacy':
            from evaluation.privacy import validate_privacy_packet
            validate_privacy_packet(args.capture, args.packet)
            print('Reviewed bytes and original linkage verified. This does not certify absence of personal data.')
        elif args.command == 'support-summary':
            from pilot.support import summary
            write_json(args.output, summary(args.run))
            print('Minimal support summary created. Review before sharing; nothing sent.')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f'Error: {exc}\n')
    return 0
