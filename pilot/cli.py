"""Command-line parsing and output for the local pilot state machine."""

import argparse
import json
from pathlib import Path
import sqlite3

from .reporting import compact_report


def build_parser(prog=None):
    parser = argparse.ArgumentParser(prog=prog, description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)

    command = sub.add_parser('init', help='Create a run and freeze its local configuration')
    command.add_argument('run', type=Path, nargs='?',
                         help='New run directory; with --config, defaults beside that file under runs/')
    command.add_argument('--state', type=Path)
    command.add_argument('--start')
    command.add_argument('--config', type=Path, help='Local TOML settings, frozen into the run manifest')
    command.add_argument('--workflow-version', type=int, choices=[1, 2], default=2)
    command.add_argument('--category', help='Category sampled in this run; does not exclude other project categories')
    command.add_argument('--require-direct-research-capture', action='store_true',
                         help='Require READY-covered direct browser capture packets for reviewer research')

    command = sub.add_parser('import-discovery', help='Import rows and coverage from a local discovery folder')
    command.add_argument('run')
    command.add_argument('--discovery-dir', type=Path, help='Folder containing rows.json and summary.json; default: RUN/discovery')
    command.add_argument('--rows', type=Path, help='Explicit rows JSON; requires --summary')
    command.add_argument('--summary', type=Path, help='Explicit coverage JSON; requires --rows')

    command = sub.add_parser('select')
    command.add_argument('run')
    command.add_argument('identities', nargs='+')
    command.add_argument('--carryover', action='store_true')
    command = sub.add_parser('triage')
    command.add_argument('run')
    command.add_argument('identity')
    command.add_argument('disposition', choices=['deferred', 'rejected'])
    command.add_argument('reason')
    command = sub.add_parser('backlog')
    command.add_argument('--state', type=Path)
    command.add_argument('--config', type=Path)

    command = sub.add_parser('bind')
    command.add_argument('run')
    command.add_argument('identity')
    command.add_argument('capture')
    command.add_argument('--sanitized', type=Path, help='Manually sanitized privacy workspace')
    command.add_argument('--privacy-review', type=Path, help='Completed privacy review matching that workspace')
    command = sub.add_parser('attest')
    command.add_argument('run')
    command.add_argument('identity')
    command.add_argument('checks')

    for name in ('job', 'dispatched'):
        command = sub.add_parser(name)
        command.add_argument('run')
        command.add_argument('identity')
        command.add_argument('role', choices=['reviewer', 'valuator'])
        if name == 'dispatched':
            command.add_argument('agent_id')
    for name in ('complete-review', 'complete-valuation'):
        command = sub.add_parser(name)
        command.add_argument('run')
        command.add_argument('identity')
    for name in ('stop', 'stop-agent', 'reopen-review'):
        command = sub.add_parser(name)
        command.add_argument('run')
        command.add_argument('identity')
        if name != 'stop':
            command.add_argument('agent_id')
        command.add_argument('reason')
        if name == 'stop':
            command.add_argument('--status', choices=['failed', 'skipped', 'unresolved'], default='unresolved')
    command = sub.add_parser('report')
    command.add_argument('run')
    command.add_argument('--full', action='store_true')
    command = sub.add_parser('status', help='Inspect current state and available next steps without changing the run')
    command.add_argument('run')
    return parser


def discovery_paths(run, rows=None, summary=None, discovery_dir=None):
    """Resolve one explicit file pair or one folder; never silently mix modes."""
    if (rows is None) != (summary is None):
        raise ValueError('Explicit discovery files require both --rows and --summary')
    if rows is not None:
        if discovery_dir is not None:
            raise ValueError('Choose --discovery-dir or the --rows/--summary pair')
        return Path(rows), Path(summary)
    directory = Path(discovery_dir) if discovery_dir is not None else Path(run) / 'discovery'
    return directory / 'rows.json', directory / 'summary.json'


def main(argv=None, *, prog=None):
    # Resolve queue functions at dispatch time, preserving the existing Python API
    # and callers that replace queue.now/session while exercising the workflow.
    from . import queue
    parser = build_parser(prog)
    args = vars(parser.parse_args(argv))
    command = args.pop('command')
    full = args.pop('full', False)
    functions = {
        'init': queue.init, 'select': queue.select, 'triage': queue.triage,
        'backlog': queue.backlog, 'bind': queue.bind, 'attest': queue.attest,
        'job': queue.job, 'dispatched': queue.dispatched, 'complete-review': queue.complete_review,
        'complete-valuation': queue.complete_valuation, 'stop': queue.stop,
        'stop-agent': queue.stop_agent, 'reopen-review': queue.reopen_review, 'report': queue.report,
    }
    try:
        if command == 'init':
            if args['config'] is not None:
                config_path = args['config'].resolve()
                configuration = queue.load_config(config_path)
                if args['category'] is None:
                    categories = configuration['search']['categories']
                    if len(categories) != 1:
                        raise ValueError('Choose --category when the configuration lists multiple categories')
                    args['category'] = categories[0]
                if args['run'] is None:
                    stamp = queue.utc(queue.now()).strftime('%Y%m%dT%H%M%S.%fZ')
                    args['run'] = config_path.parent / 'runs' / stamp
                args['config'] = configuration
            elif args['run'] is None:
                raise ValueError('Supply a run directory or --config to choose its workspace')
            result = {**queue.init(**args), 'run_path': str(args['run'].resolve())}
        elif command == 'status':
            from .status import snapshot
            result = snapshot(args['run'])
        elif command == 'import-discovery':
            rows, summary = discovery_paths(**args)
            result = queue.import_discovery(args['run'], rows, summary)
        elif command == 'attest':
            result = queue.attest(args['run'], args['identity'], args['checks'])
        else:
            result = functions[command](**args)
        if command == 'report' and not full:
            result = compact_report(result, args['run'])
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (ValueError, KeyError, OSError, sqlite3.Error, TypeError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        return 1
