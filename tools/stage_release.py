"""Build a local source candidate from an explicit allowlist; never publishes."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat


FORBIDDEN_PARTS = {'.git', 'private', 'data', 'runs', 'research', 'validation',
                   'sessions', 'browser-profile', 'browser-profiles', '__pycache__',
                   '.venv', 'dist', 'build'}
TEXT_SUFFIXES = {'.py', '.md', '.toml', '.json', '.txt', '.yml', '.yaml', '.mjs', '.in'}
SECRET_FORMAT = re.compile(r'\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{24,}|'
                           r'github_pat_[A-Za-z0-9_]{24,}|AKIA[A-Z0-9]{16})\b')
USER_PATH = re.compile(r'(?i)(?:[a-z]:[\\/]+users[\\/]+[^\s"<>\\/]+|/(?:home|Users)/[^/\s"<>]+)')
CHAT_LINK = re.compile(r'https://chatgpt\.com/(?:c|share)/[A-Za-z0-9-]+')
PRIVATE_KEY = re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')
EMAIL = re.compile(r'(?i)[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,})')


def is_link(path):
    """Reject all Windows reparse points, including junctions on Python 3.11."""
    try:
        attributes = getattr(path.lstat(), 'st_file_attributes', 0)
    except FileNotFoundError:
        attributes = 0
    return path.is_symlink() or bool(attributes & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


def findings(name, content, private_patterns=()):
    """Return locations/categories only. Never echo a matched sensitive value."""
    results = []
    for line_no, line in enumerate(content.splitlines(), 1):
        for label, pattern in [('credential_format', SECRET_FORMAT), ('user_directory', USER_PATH),
                               ('conversation_link', CHAT_LINK), ('private_key', PRIVATE_KEY)]:
            if pattern.search(line):
                results.append({'file': name, 'line': line_no, 'kind': label})
        for match in EMAIL.finditer(line):
            domain = match.group(1).lower()
            # Reserved example domains and a synthetic URL userinfo test are not contacts.
            if (domain in {'example.com', 'example.org', 'example.net'} or
                    domain.endswith(('.example', '.invalid', '.test')) or
                    line[max(0, match.start() - 3):match.start()] == '://'):
                continue
            results.append({'file': name, 'line': line_no, 'kind': 'email_address'})
        if any(pattern and pattern.casefold() in line.casefold() for pattern in private_patterns):
            results.append({'file': name, 'line': line_no, 'kind': 'local_private_marker'})
    return results


def read_allowlist(root, manifest):
    data = json.loads(manifest.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('schema_version') != 1:
        raise ValueError('Unknown release allowlist schema')
    names = data.get('files')
    if (not isinstance(names, list) or not names or
            not all(isinstance(n, str) for n in names) or
            len(names) != len(set(n.casefold() for n in names))):
        raise ValueError('Release files must be a nonempty unique list')
    snapshot = {}
    for name in names:
        rel = PurePosixPath(name)
        if (not name or '\\' in name or ':' in name or rel.is_absolute() or
                name != rel.as_posix() or any(part in {'.', '..'} for part in rel.parts) or
                any(part.lower() in FORBIDDEN_PARTS for part in rel.parts)):
            raise ValueError('Disallowed release path: ' + name)
        if (name.lower().endswith(('.local.toml', '.local.json')) or rel.name.startswith('.env') or
                (rel.suffix not in TEXT_SUFFIXES and name not in {'.gitignore', 'LICENSE'})):
            raise ValueError('Release file type is not approved: ' + name)
        path = root.joinpath(*rel.parts)
        if any(is_link(part) for part in [path, *path.parents] if part != root and part.is_relative_to(root)):
            raise ValueError('Symlinks are not allowed in release inputs: ' + name)
        if not path.resolve().is_relative_to(root) or not path.is_file():
            raise ValueError('Missing or escaping release input: ' + name)
        content = path.read_bytes()
        content.decode('utf-8')  # No unchecked binary/media payloads in this source candidate.
        snapshot[name] = content
    return snapshot


def stage(root, destination, manifest=None, private_patterns=()):
    root = Path(root).absolute()
    if any(is_link(p) for p in (root, *root.parents)):
        raise ValueError('Release root cannot traverse symlinks or junctions')
    root = root.resolve()
    destination = Path(destination)
    if destination.exists() or is_link(destination):
        raise FileExistsError('Release destination already exists')
    if any(is_link(p) for p in destination.parents):
        raise ValueError('Release destination cannot traverse symlinks or junctions')
    destination = destination.resolve()
    snapshot = read_allowlist(root, Path(manifest or root / 'release-files.json'))
    issues = [issue for name, content in snapshot.items()
              for issue in findings(name, content.decode('utf-8'), private_patterns)]
    if issues:
        raise ValueError('Release scan rejected files (values withheld): ' + json.dumps(issues))
    # Freeze bytes before writing; a concurrent edit cannot change the checked candidate.
    destination.mkdir(parents=True, exist_ok=False)
    for name, content in snapshot.items():
        out = destination / name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(content)
    report = {
        'schema_version': 1, 'status': 'local_candidate', 'published': False,
        'files': [{'path': name, 'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
                  for name, content in sorted(snapshot.items())],
        'checks': {'explicit_file_allowlist': True, 'text_patterns_passed': True,
                   'binary_media_excluded': True},
        'limitations': ['Pattern scans are not a complete secret or personal-data audit.',
                       'No Git history, remote repository or source-access permission is checked.',
                       'This check creates local artifacts only; it does not publish them.'],
    }
    (destination / 'SOURCE-MANIFEST.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path, help='New local directory; existing directories are refused')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--private-patterns', type=Path, help='Private JSON array of owner-specific strings to reject')
    args = parser.parse_args(argv)
    patterns = []
    if args.private_patterns:
        patterns = json.loads(args.private_patterns.read_text(encoding='utf-8'))
        if not isinstance(patterns, list) or not all(isinstance(x, str) and x.strip() for x in patterns):
            parser.error('Private patterns must be a JSON array of nonempty strings')
    try:
        result = stage(args.root, args.destination, args.manifest, patterns)
    except (ValueError, OSError) as exc:
        parser.exit(1, str(exc) + '\n')
    print(f"Staged {len(result['files'])} reviewed-list files locally. Nothing published.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
