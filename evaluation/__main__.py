"""Run local evaluation gates or prepare a price-masked draft. No model/API calls."""

import argparse
import json
from pathlib import Path

from .identification import validate_identification
from .packet import export_packet
from .service import evaluate_capture


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    mask = commands.add_parser('prepare')
    mask.add_argument('capture', type=Path)
    mask.add_argument('destination', type=Path)
    check = commands.add_parser('check')
    check.add_argument('review', type=Path, help='Review JSON; current relative source_capture paths resolve beside the review')
    check.add_argument('--output', type=Path, required=True)
    check.add_argument('--legacy-replay', action='store_true',
                       help='Replay schema-1 declarations with historical policy; does not verify retained evidence files')
    policy_source = check.add_mutually_exclusive_group()
    policy_source.add_argument('--config', type=Path, help='Use validated local economic policy')
    policy_source.add_argument('--run', type=Path, help='Use the economic policy frozen in this run')
    audit = commands.add_parser('audit-identification')
    audit.add_argument('input', type=Path)
    audit.add_argument('result', type=Path)
    audit.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == 'prepare':
        export_packet(args.capture, args.destination)
        print(f'{args.destination.resolve()} — draft requires text/photo price-leak review')
    elif args.command == 'audit-identification':
        result = validate_identification(json.loads(args.result.read_text(encoding='utf-8')), args.input.read_bytes())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
        print('Contract valid' if result['contract_valid'] else 'Contract rejected: ' + ', '.join(result['errors']))
        return 0 if result['contract_valid'] else 1
    else:
        review = json.loads(args.review.read_text(encoding='utf-8-sig'))
        capture = Path(review['source_capture'])
        # Original schema-1 commands interpreted source_capture against the cwd.
        # Current drafts use absolute paths; portable demos use the review folder.
        if not capture.is_absolute() and not args.legacy_replay:
            capture = args.review.resolve().parent / capture
        result = evaluate_capture(capture, args.review, args.config, args.run,
                                  legacy_replay=args.legacy_replay)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
        label = 'Historical replay only — ' if args.legacy_replay else ''
        print(f"{label}{result['outcome']}: {', '.join(result['reasons'])}")


if __name__ == '__main__':
    raise SystemExit(main())
