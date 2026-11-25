"""Verify the digests of the checked-in delivery snapshot using Python stdlib."""
import hashlib
from pathlib import Path, PurePosixPath
import re

ROOT = Path(__file__).resolve().parents[1]


def check(root=ROOT):
    listed = set()
    for line in (root / 'MANIFEST.sha256').read_text(encoding='utf-8').splitlines():
        h, filename = line.split('  ', 1)
        rel = PurePosixPath(filename)
        if (not re.fullmatch(r'[a-f0-9]{64}', h) or filename in listed or not rel.parts
                or rel.is_absolute() or '..' in rel.parts or ':' in filename or '\\' in filename):
            raise ValueError(f'invalid manifest row: {filename}')
        target = (root / filename).resolve()
        if not target.is_relative_to(root.resolve()):
            raise ValueError(f'file escapes package: {filename}')
        if hashlib.sha256(target.read_bytes()).hexdigest() != h:
            raise ValueError(f'file has changed: {filename}')
        listed.add(filename)
    if not listed:
        raise ValueError('empty manifest')
    return len(listed)


if __name__ == '__main__':
    try:
        print(f'PASS: {check()} files match MANIFEST.sha256')
    except (OSError, ValueError) as e:
        raise SystemExit(f'FAIL: {e}') from e
