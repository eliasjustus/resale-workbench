"""Stage, build and test a fresh candidate. No dependency installation or publishing."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

if __package__:
    from .stage_release import is_link, stage
else:
    from stage_release import is_link, stage


def verified_bytes(destination, manifest, manifest_bytes):
    """Use the original in-memory inventory, never a potentially edited manifest."""
    manifest_path = destination / 'SOURCE-MANIFEST.json'
    if is_link(manifest_path) or manifest_path.read_bytes() != manifest_bytes:
        raise ValueError('Staged SOURCE-MANIFEST.json changed during verification')
    contents = {}
    for item in manifest['files']:
        path = destination / item['path']
        if (any(is_link(p) for p in [path, *path.parents] if p.is_relative_to(destination))
                or not path.resolve().is_relative_to(destination)):
            raise ValueError('Staged source became a link or escaped: ' + item['path'])
        data = path.read_bytes()
        if len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('Staged source changed during verification: ' + item['path'])
        contents[item['path']] = data
    contents['SOURCE-MANIFEST.json'] = manifest_bytes
    return contents


def write_report(path, report):
    """Never leave a partially written success declaration behind."""
    data = (json.dumps(report, indent=2) + '\n').encode('utf-8')
    created = False
    try:
        with path.open('xb') as output:
            created = True
            output.write(data)
    except OSError:
        if created:
            path.unlink(missing_ok=True)
        raise


def check_candidate(root, destination, python=sys.executable, private_patterns=()):
    destination = Path(destination).absolute()
    manifest = stage(root, destination, private_patterns=private_patterns)
    destination = destination.resolve()
    manifest_bytes = (destination / 'SOURCE-MANIFEST.json').read_bytes()
    verification = destination / 'verification'
    verification.mkdir()
    report = {'schema_version': 1, 'status': 'failed', 'published': False, 'steps': [],
              'source_manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
              'limits': ['No dependencies are installed. Build/test dependencies must already be available.',
                         'Offline checks do not validate live sources, model availability or economic accuracy.']}
    archive_path = destination / 'source.zip'
    archive_created = False
    try:
        commands = [('build', [str(python), '-m', 'build', '--no-isolation']),
                    ('tests', [str(python), '-m', 'unittest', 'discover', '-s', 'tests', '-v'])]
        for name, command in commands:
            log = verification / (name + '.log')
            with log.open('x', encoding='utf-8') as output:
                result = subprocess.run(command, cwd=destination, stdout=output,
                                        stderr=subprocess.STDOUT, check=False,
                                        env=dict(os.environ, PYTHONIOENCODING='utf-8'))
            report['steps'].append({'name': name, 'command': command,
                                    'returncode': result.returncode, 'log': f'verification/{log.name}'})
            if result.returncode:
                raise ValueError(f'{name} failed with exit code {result.returncode}; see {log}')
            verified_bytes(destination, manifest, manifest_bytes)
            if name == 'build':
                wheels = list((destination / 'dist').glob('*.whl'))
                sources = list((destination / 'dist').glob('*.tar.gz'))
                if len(wheels) != 1 or len(sources) != 1:
                    raise ValueError('Build must produce exactly one wheel and one source distribution')
                report['artifacts'] = [str(path.relative_to(destination)) for path in wheels + sources]
        contents = verified_bytes(destination, manifest, manifest_bytes)
        # Snapshot bytes avoid a concurrent edit changing an already checked archive input.
        with archive_path.open('xb') as output:
            archive_created = True
            with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in sorted(contents.items()):
                    archive.writestr(name, data)
        report.update(status='passed', source_files_unchanged=True,
                      source_archive='source.zip',
                      source_archive_sha256=hashlib.sha256(archive_path.read_bytes()).hexdigest())
        write_report(verification / 'report.json', report)
        return report
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        if archive_created:
            archive_path.unlink(missing_ok=True)
        report.update(status='failed', error=str(exc))
        write_report(verification / 'report.json', report)
        raise ValueError(f'Candidate checks failed: {exc}. Candidate and logs retained at {destination}') from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path, help='New candidate directory; existing destinations are refused')
    parser.add_argument('--python', default=sys.executable, help='Python with package, build and test dependencies already installed')
    parser.add_argument('--private-patterns', type=Path, help='Private JSON array of owner-specific strings to reject')
    args = parser.parse_args(argv)
    try:
        patterns = json.loads(args.private_patterns.read_text(encoding='utf-8')) if args.private_patterns else []
        if not isinstance(patterns, list) or not all(isinstance(p, str) and p.strip() for p in patterns):
            raise ValueError('Private patterns must be a JSON array of nonempty strings')
        result = check_candidate(Path(__file__).resolve().parents[1], args.destination, args.python, patterns)
    except (OSError, ValueError) as exc:
        parser.exit(1, f'{exc}\n')
    print(f"Candidate checks {result['status']}: {args.destination.resolve() / 'verification' / 'report.json'}")
    print(f"Source archive: {args.destination.resolve() / result['source_archive']}. Nothing published.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
