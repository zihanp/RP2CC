"""Replay the published study offline with Python 3.10+ and its standard library.

No SDK, target tools, Lean, pip modules, network, device, or file writes. Run
from any directory. This checks recorded evidence, not a fresh hardware run.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools'), str(ROOT / 'formal')]

from audit_firmware import audit
import bench
from check_hazard3_code import check_words, selected_names
from instances import emit as lean_instances
from rp2cc.driver import verify_artifacts
from tune_hazard3 import verify as verify_selection


SOURCE = ROOT / 'examples/bench.json'
GEN = ROOT / 'dist/generated'
FIRMWARE = ROOT / 'dist/firmware'
CAPTURES = ROOT / 'dist/bench'
EVIDENCE = ROOT / 'dist/evidence'
IMAGE_FILES = {'rp_lab.elf', 'rp_lab.uf2', 'rp_lab.bin'}
DEVELOPMENT_LISTINGS = {'rp_lab.elf.map', 'rp_lab.dis'}
DEPLOYMENTS = {
    'hw973b-arm': ('hw973b', 'arm'), 'hw973b-cdc': ('hw973b', 'arm'),
    'hw973b-riscv': ('hw973b', 'riscv'), 'hw973b-riscv-tuned': ('hw973b', 'riscv'),
    'pico2-arm': ('pico2', 'arm'), 'rp2350-arm': ('rp2350', 'arm'),
    'rp2350-riscv': ('rp2350', 'riscv'), 'rp2350b-arm': ('rp2350b', 'arm'),
    'rp2354a-arm': ('rp2354a', 'arm'), 'rp2354a-riscv': ('rp2354a', 'riscv'),
    'rp2354a-riscv-tuned': ('rp2354a', 'riscv'), 'sdk-contract-arm': ('rp2354a', 'arm'),
    'sdk-contract-riscv': ('rp2354a', 'riscv'),
}
CAPTURE_ARCH = {'arm': 'arm', 'riscv': 'riscv', 'riscv-tuned': 'riscv'}


def require(condition, why):
    if not condition:
        raise ValueError(why)


def sha_file(path):
    return sha256(path.read_bytes()).hexdigest()


def document(path):
    def unique(pairs):
        obj = dict(pairs)
        require(len(obj) == len(pairs), f'duplicate JSON key: {path}')
        return obj
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique)


def same(actual, expected, what):
    # JSON comparison preserves the difference between e.g. true and 1.
    require(json.dumps(actual, sort_keys=True, allow_nan=False) ==
            json.dumps(expected, sort_keys=True, allow_nan=False), f'{what} does not match replay')


class LinkedELF:
    """Small ELF32-LE executable reader for file-backed, mapped function bytes."""
    def __init__(self, path, arch):
        self.path, self.arch, self.raw = path, arch, path.read_bytes()
        head = struct.unpack_from('<16sHHIIIIIHHHHHH', self.raw)
        ident, typ, machine, ver, _, ph, sh, _, ehsize, ps, np, ss, ns, _ = head
        require(ident[:7] == b'\x7fELF\x01\x01\x01' and typ == 2 and ver == 1 and
                machine == {'arm': 40, 'riscv': 243}[arch] and ehsize == 52,
                f'{path}: expected a linked {arch} 32-bit LE executable')
        require(ps == 32 and np and ph + ps * np <= len(self.raw) and
                ss == 40 and ns and sh + ss * ns <= len(self.raw), f'{path}: invalid ELF tables')
        self.loads = [p for p in struct.iter_unpack('<8I', self.raw[ph:ph + ps * np]) if p[0] == 1]
        self.sections = list(struct.iter_unpack('<10I', self.raw[sh:sh + ss * ns]))

    def section_bytes(self, section):
        start, size = section[4:6]
        require(start + size <= len(self.raw), f'{self.path}: truncated file-backed section')
        return self.raw[start:start + size]

    def functions(self, names):
        wanted = {s.encode('ascii'): s for s in names}
        found = {}
        tables = [s for s in self.sections if s[1] == 2]  # SHT_SYMTAB
        require(len(tables) == 1, f'{self.path}: expected one static symbol table')
        tab = tables[0]
        require(tab[9] == 16 and tab[5] % 16 == 0 and tab[6] < len(self.sections), 'invalid symbol table')
        strings = self.sections[tab[6]]
        require(strings[1] == 3, 'invalid symbol string table')
        strings = self.section_bytes(strings)
        for n, value, size, info, _, idx in struct.iter_unpack('<IIIBBH', self.section_bytes(tab)):
            if info & 15 != 2:  # STT_FUNC
                continue
            end = strings.find(b'\0', n)
            require(end >= n, 'invalid ELF symbol name')
            name = wanted.get(strings[n:end])
            if name is None:
                continue
            require(name not in found and 0 < idx < len(self.sections), f'{name}: missing/ambiguous section')
            sec = self.sections[idx]
            va = value & ~1
            require(value & 1 == int(self.arch == 'arm'), f'{name}: wrong Thumb/RISC-V alignment')
            off = va - sec[3]
            require(sec[1] == 1 and sec[2] & 6 == 6 and 0 <= off < off + size <= sec[5],
                    f'{name}: not in allocated executable section')
            start = sec[4] + off
            require(any(p[6] & 1 and p[2] <= va and va + size <= p[2] + p[4]
                        and p[1] + va - p[2] == start for p in self.loads),
                    f'{name}: bytes are not mapped by an executable PT_LOAD')
            for r in self.sections:
                if r[1] in (4, 9) and r[7] in (0, idx):  # SHT_RELA/SHT_REL
                    step = 12 if r[1] == 4 else 8
                    require(r[9] == step and r[5] % step == 0, f'{name}: malformed relocation table')
                    data = self.section_bytes(r)
                    require(not any(va <= struct.unpack_from('<I', data, i)[0] < va + size
                                    for i in range(0, len(data), step)), f'{name}: unresolved code relocation')
            found[name] = (va, self.section_bytes(sec)[off:off + size])
        require(set(found) == set(names), f'{self.path}: missing symbols {set(names) - set(found)}')
        return found


def audited_image(folder, arch, board, kind, generated_c_hash):
    m = document(folder / 'manifest.json')
    require((m['architecture'], m['board'], m['kind']) == (arch, board, kind), f'{folder}: target profile')
    require(set(m['files']) in (IMAGE_FILES, IMAGE_FILES | DEVELOPMENT_LISTINGS),
            f'{folder}: unexpected or incomplete image file list')
    require(m['generated_c_sha256'] == generated_c_hash, f'{folder}: generated C hash')
    # Verifies SHA/size of every included file, checks the UF2 platform,
    # bounds and E10 marker, and compares every UF2 load byte and pad against ELF.
    return audit(folder), m


def capture(name, arch, c_hash):
    folder = CAPTURES / name
    _, manifest = audited_image(folder / 'image', arch, 'rp2350', 'sram-only', c_hash)
    raw = (folder / 'mailbox.bin').read_bytes()
    require(len(raw) == bench.NWORDS * 4, 'raw mailbox length')
    nonce, = struct.unpack_from('<I', raw, 8)  # From raw bytes, not results.json.
    require(nonce != 0, 'invalid zero run nonce')
    decoded = bench.decode(raw, nonce)
    saved = document(folder / 'results.json')
    for k, v in decoded.items():
        same(saved[k], v, f'{name}/results.json:{k}')
    require(decoded['cpu_id_register'] == ('CPUID' if arch == 'arm' else 'mimpid') and
            decoded['io_pass_mask'] == 15, f'{name}: wrong core or I/O test bits')
    image = LinkedELF(folder / 'image/rp_lab.elf', arch)
    linked = document(folder / 'linked_code.json')
    require(linked['arch'] == arch and saved['input_sha256'] == sha_file(SOURCE) and
            linked['sha256'] == saved['elf_sha256'] == manifest['files']['rp_lab.elf']['sha256']
            == sha256(image.raw).hexdigest(), f'{name}: ELF/manifest/IR hash linkage')
    if 'elf_release' in manifest:
        provenance = manifest['elf_release']
        require(provenance['original_sha256'] == saved.get('original_elf_sha256') and
                provenance['entry_and_load_segments_unchanged'] is True, f'{name}: ELF release provenance')
    segments = []
    for _, _, va, pa, fs, ms, _, _ in image.loads:
        if not ms:
            continue
        for addr in (va, pa):
            require(bench.BASE <= addr < bench.END and fs <= ms <= bench.END - addr and
                    (addr >= bench.BOX_END or addr + ms <= bench.BOX), 'capture image overlaps mailbox/outside SRAM')
        segments.append(dict(paddr=hex(pa), vaddr=hex(va), memsz=ms, filesz=fs))
    same(linked['segments'], segments, f'{name}/linked_code.json:segments')
    funcs = image.functions(bench.CASES)
    require(set(funcs) == set(linked['symbols']), 'linked function inventory differs')
    for symbol, (va, data) in funcs.items():
        row = linked['symbols'][symbol]
        require((row['address'], row['symbol_bytes'], row['bytes_hex']) == (hex(va), len(data), data.hex()),
                f'{name}/{symbol}: linked bytes/address/size')
        pos = 0
        for line in row['disassembly']:
            addr, hexdump, _ = line.split(None, 2)
            chunk = bytes.fromhex(hexdump)
            require(chunk and int(addr, 16) == va + pos and data[pos:pos + len(chunk)] == chunk,
                    f'{name}/{symbol}: listing bytes/address')
            pos += len(chunk)
        require(pos == len(data), f'{name}/{symbol}: incomplete listing')
    return decoded


def tuned_leaf(path, prefix, selection, receipt_hash, evidence):
    elf = LinkedELF(path, 'riscv')
    functions = elf.functions([prefix + n for n in selected_names(selection)])
    recomputed = {'format': 'rp2cc.hazard3.binary-audit.v1', 'elf_sha256': sha256(elf.raw).hexdigest(),
                  'receipt_sha256': receipt_hash, 'elf_symbols': {n: check_words(b) for n, (_, b) in functions.items()}}
    same(document(EVIDENCE / evidence), recomputed, evidence)
    return [v['bytes'] for v in recomputed['elf_symbols'].values()]


FORMAL_FILES = {'Proofs.lean', 'Checker.lean', 'PoolExample.lean', 'Hazard3.lean', 'Replay.lean', 'Audit.lean'}
DRIVERS = {'check.py', 'instances.py'}
THEOREMS = {'floor_correct', 'lane_is_floor', 'decoded_floors', 'affine_correct', 'dirty_restore',
            'slice_sound', 'subst_sound', 'norm_sound', 'accepts_sound', 'demo_lower84', 'demo_attains84',
            'hazard3_correct'}


def formal_report(report_name, source_name, certificates):
    r = document(ROOT / report_name)
    for key, names in [('lean_source_sha256', FORMAL_FILES), ('validation_driver_sha256', DRIVERS)]:
        same(r[key], {n: sha_file(ROOT / 'formal' / n) for n in names}, f'{report_name}:{key}')
    require(set(r['audited_theorems']) == THEOREMS and
            all(set(a) <= {'propext', 'Classical.choice', 'Quot.sound'} for a in r['audited_theorems'].values()),
            f'{report_name}: unexpected theorem or axiom inventory')
    source = ROOT / source_name
    require(r['source'] == source_name and r['source_file_sha256'] == sha_file(source), f'{report_name}: source')
    same([c['certificate'] for c in r['replayed']], certificates, f'{report_name}:certificate inventory')
    count = 0
    for row in r['replayed']:
        path = ROOT / row['certificate']
        require(row['sha256'] == sha_file(path), f'{report_name}: certificate hash {path}')
        verify_artifacts(source, path)
        text, n = lean_instances(source, path)
        # check.py historically wrote native text-mode line endings. Reconstruct
        # both serializations so this also works when replayed on another OS.
        hashes = {sha256(text.replace('\n', eol).encode('utf-8')).hexdigest() for eol in ('\n', '\r\n')}
        require(row['instance_lean_sha256'] in hashes and row['kernel_reduced_instance_theorems'] == n,
                f'{report_name}: generated Lean instance source or theorem count')
        count += n
    return count


def study():
    certs = {mode: verify_artifacts(SOURCE, GEN / mode / 'certificate.json') for mode in ('baseline', 'optimized')}
    require(all(c['mode'] == mode for mode, c in certs.items()), 'swapped or wrong certificate modes')
    pools = [len(bytes.fromhex(c['pool']['bytes'])) for c in certs.values()]
    print(f'PASS portable compiler: 2 certificates and regenerated C/H; pools {pools[0]} -> {pools[1]} bytes')
    tuned = GEN / 'hazard3'
    selection = verify_selection(SOURCE, GEN / 'optimized', tuned)
    c_hashes = {False: certs['optimized']['files']['kernels.c'], True: selection['target_files']['kernels.c']}
    receipt_hash = sha_file(tuned / 'hazard3.json')
    rows = []
    available = {f.parent.name for f in FIRMWARE.glob('*/manifest.json')}
    require(available == set(DEPLOYMENTS), 'deployment manifest inventory differs')
    for target, (board, arch) in sorted(DEPLOYMENTS.items()):
        rec, _ = audited_image(FIRMWARE / target, arch, board, 'flash', c_hashes[target.endswith('-tuned')])
        rows.append(rec)
    same(document(FIRMWARE / 'audit.json'), {'schema': 'rp2lab.firmware.audit.v1', 'images': rows}, 'firmware/audit.json')
    print(f'PASS deployment images: {len(rows)} Flash ELF/UF2 pairs; {sum(i["elf_load_bytes_compared"] for i in rows):,} ELF load bytes checked')
    for name, arch in CAPTURE_ARCH.items():
        try:
            d = capture(name, arch, c_hashes[name.endswith('-tuned')])
        except (KeyError, OSError, ValueError, struct.error) as ex:
            raise ValueError(f'{name}: {ex}') from ex
        print(f'PASS {name}: raw mailbox, {d["host_verified_sample_digests"]} recomputed sample checksums, '
              f'all cycle statistics, {len(bench.CASES)} ELF functions and SRAM UF2; '
              f'floor2 median {d["summary"]["op_floor2"]["median_cycles_per_call"]} cycles/call')
    leafs = [(CAPTURES / 'riscv-tuned/image/rp_lab.elf', 'op_', 'hazard3-linked.json'),
             (FIRMWARE / 'hw973b-riscv-tuned/rp_lab.elf', 'rp_', 'hazard3-hw973b-deployment.json'),
             (FIRMWARE / 'rp2354a-riscv-tuned/rp_lab.elf', 'rp_', 'hazard3-rp2354a-deployment.json')]
    sizes = [tuned_leaf(p, prefix, selection, receipt_hash, e) for p, prefix, e in leafs]
    size_text = ' / '.join('+'.join(map(str, lengths)) for lengths in sizes)
    print(f'PASS Hazard3 selector replay, portable provenance and opcode decode: {size_text} bytes '
          '(benchmark / HW-973B / RP2354A final ELF)')
    main_n = formal_report('formal/verification.json', 'examples/bench.json',
                           [f'dist/generated/{m}/certificate.json' for m in certs])
    math_n = formal_report('dist/evidence/math-frontend-lean.json', 'dist/generated/jump.json',
                           ['dist/generated/jump/certificate.json'])
    project_n = formal_report('dist/math-project/lean.json', 'dist/math-project/ir.json',
                              ['dist/math-project/generated/certificate.json'])
    print(f'PASS three formal reports: hashes of {len(FORMAL_FILES)} Lean sources, {len(DRIVERS)} validation drivers, '
          f'inputs, certificates and reconstructed instance sources; {len(THEOREMS)} audited core theorem names; '
          f'{main_n} benchmark instances, {math_n}/{project_n} in the two math-example reports')
    print('Offline replay complete (no hardware run or Lean compilation). To recheck the proofs with Lean installed:')
    print('  python formal/check.py --self-test --kernel-instances')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        study()
    except (OSError, ValueError, KeyError, IndexError, TypeError, struct.error, RecursionError) as exc:
        print(f'FAIL verify_study: {exc}', file=sys.stderr)
        raise SystemExit(1)
