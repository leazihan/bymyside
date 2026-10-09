#!/usr/bin/env python3
"""Archive reviewed history only; default is a read-only preview."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile
from datetime import date, datetime, timedelta

LOG = re.compile(r'^\d{4}-\d{2}-\d{2}\.md$')
REPORT = re.compile(r'^(?:morning|evening-review|weekly-review|midday|diagnostic)-\d{4}-\d{2}-\d{2}\.md$')


def reviewed(text):
    if not text.startswith('---\n'):
        return False
    parts = text.split('---', 2)
    return len(parts) == 3 and re.search(r'^archive_ready:\s*true\s*$', parts[1], re.M) is not None


def candidates(root, today, keep_days):
    cutoff = today - timedelta(days=keep_days - 1)
    result = []
    pending = []
    for folder, pattern in [(root / '.bymyside/memory', LOG), (root / 'outputs', REPORT)]:
        if folder.is_symlink():
            raise ValueError('Refusing symlinked history directory')
        if not folder.exists():
            continue
        for path in sorted(folder.glob('*.md')):
            if path.is_symlink() or not pattern.fullmatch(path.name):
                continue
            stamp = re.search(r'\d{4}-\d{2}-\d{2}', path.name).group()
            if date.fromisoformat(stamp) >= cutoff:
                continue
            if not reviewed(path.read_text(encoding='utf-8')):
                pending.append(str(path.relative_to(root)))
                continue
            data = path.read_bytes()
            result.append((path, data, hashlib.sha256(data).hexdigest()))
    return result, pending


def run(root, today, keep_days, apply):
    root = root.resolve()
    selected, pending = candidates(root, today, keep_days)
    report = {'mode': 'apply' if apply else 'preview', 'files': [str(p.relative_to(root)) for p, _, _ in selected], 'needs_review': pending}
    if not apply or not selected:
        return report
    archive = root / '.bymyside/archive'
    if archive.is_symlink():
        raise ValueError('Refusing symlinked archive directory')
    archive.mkdir(parents=True, exist_ok=True)
    target = archive / ('history-' + today.isoformat() + '-' + datetime.now().strftime('%H%M%S%f') + '.tar.gz')
    manifest = []
    with tarfile.open(target, 'x:gz') as tar:
        for path, data, digest in selected:
            name = str(path.relative_to(root))
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
            manifest.append({'path': name, 'sha256': digest})
        payload = json.dumps(manifest, ensure_ascii=False).encode()
        info = tarfile.TarInfo('recovery-manifest.json')
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    with tarfile.open(target, 'r:gz') as tar:
        for item in manifest:
            entry = tar.extractfile(item['path'])
            if entry is None or hashlib.sha256(entry.read()).hexdigest() != item['sha256']:
                raise ValueError('Archive verification failed; source files retained')
    # All source files must still match before removing any originals.
    for path, _, digest in selected:
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('History changed during archival; source files retained')
    for path, _, _ in selected:
        path.unlink()
    report['archive'] = str(target.relative_to(root))
    report['bytes'] = target.stat().st_size
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--today', type=date.fromisoformat, required=True, help='User-local date, YYYY-MM-DD')
    parser.add_argument('--keep-days', type=int, default=7)
    parser.add_argument('--apply', action='store_true', help='Verify a recoverable archive before removing source history')
    args = parser.parse_args()
    if args.keep_days < 1:
        parser.error('--keep-days must be positive')
    print(json.dumps(run(args.workspace, args.today, args.keep_days, args.apply), ensure_ascii=False))


if __name__ == '__main__':
    main()
