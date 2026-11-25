"""Run the independent host checks and persist a fresh, machine-readable receipt.

From the project directory: python tests/test_compiler_report.py --out dist/evidence/compiler.json
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', type=Path, required=True)
    options = ap.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from rp2cc.driver import compile_source
    from rp2cc.model import digest, load_json

    cp = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_compiler*.py', '-v'],
                        cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print(cp.stdout, end='')
    if cp.returncode: return cp.returncode
    tag = 'native differential: '
    line = next((s for s in cp.stdout.splitlines() if tag in s), None)
    if line is None:
        print('Native C differential unavailable; refusing to write a PASS receipt.', file=sys.stderr)
        return 3
    p = root / 'examples' / 'bench.json'; src = load_json(p)
    compiler = shutil.which(os.environ.get('RP2CC_HOST_CC', 'gcc')) or shutil.which('clang')
    info = subprocess.check_output([compiler, '--version'], text=True).splitlines()[0]
    report = {
        'format': 'rp2cc.host-audit.v1', 'result': 'PASS', 'python': platform.python_version(), 'system': platform.system(),
        'host_c_compiler': info, 'host_flags': ['-std=c11','-O2','-Wall','-Wextra','-Werror','(no LTO)'],
        'source': 'examples/bench.json', 'source_file_sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
        'source_canonical_sha256': digest(src), 'host_case_counts': json.loads(line.split(tag, 1)[1]),
        'suite': [s.split(' (test_compiler', 1)[0] for s in cp.stdout.splitlines() if ' (test_compiler' in s],
        'notes': ['Each of two C variants was linked as a separate translation unit with the same host -O2.',
                  'floor_word_cases counts paired input calls, not unique u32 words proven by testing.',
                  'Host correctness evidence only: not RP235x execution time or actual MCU peripheral behavior.'],
        'artifacts': {},
    }
    for mode in ('baseline', 'optimized'):
        cert, _, stats = compile_source(src, mode)
        report['artifacts'][mode] = {**stats, 'c_and_h_sha256': cert['files']}
    options.out.parent.mkdir(parents=True, exist_ok=True)
    options.out.write_text(json.dumps(report, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__': raise SystemExit(main())
