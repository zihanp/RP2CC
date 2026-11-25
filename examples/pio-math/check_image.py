"""Optional offline inspection of the supplied Hazard3 ELF; never opens a device."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.check_hazard3_code import check_elf


def need(test, why):
    if not test: raise ValueError(why)


def read_object(elf, name):
    matches = elf.get_section_by_name('.symtab').get_symbol_by_name(name) or []
    need(len(matches) == 1 and matches[0]['st_info']['type'] == 'STT_OBJECT', name)
    sym = matches[0]; va, size = sym['st_value'], sym['st_size']
    sec = elf.get_section(sym['st_shndx']); off = va - sec['sh_addr']
    need(sec['sh_type'] == 'SHT_PROGBITS' and 0 <= off <= sec['sh_size'] - size, name + ' not file-backed')
    start = sec['sh_offset'] + off
    need(any(p['p_type'] == 'PT_LOAD' and p['p_vaddr'] <= va and va + size <= p['p_vaddr'] + p['p_filesz']
             and start == p['p_offset'] + va - p['p_vaddr'] for p in elf.iter_segments()), name + ' not mapped')
    elf.stream.seek(start); return va, elf.stream.read(size)


def check(directory):
    from elftools.elf.elffile import ELFFile
    fw = directory / 'firmware'; path = fw / 'rp_lab.elf'
    m = json.loads((fw / 'manifest.json').read_bytes())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    need(m['kind'] == 'sram-only' and m['architecture'] == 'riscv' and m['files']['rp_lab.elf']['sha256'] == digest,
         'not the published SRAM/RV32 image')
    with path.open('rb') as f:
        elf = ELFFile(f)
        addr, ins = read_object(elf, 'fifo_invert_program_instructions')
        words = struct.unpack('<3H', ins)
        need(words == (0x80a0, 0xa0cf, 0x8020), 'expected pull block / mov isr,!osr / push block')
        _, desc = read_object(elf, 'fifo_invert_program')
        ptr, length, origin, version, gpio_mask = struct.unpack('<IBbBB', desc)
        # SDK 2.3.1's RP235x target marks the program for PIO v1.
        need((ptr, length, origin, version, gpio_mask) == (addr, 3, -1, 1, 0), 'PIO descriptor mismatch')
        _, table = read_object(elf, 'samples')
    rows = list(struct.iter_unpack('<III', table))
    need([r[0] for r in rows] == [0x8000ffff, 0x7fff0010, 0x000fffef, 0xfff0000f, 0], 'sample inputs changed')
    for x, scaled, inverted in rows:
        lo, hi = struct.unpack('<hh', struct.pack('<I', x))
        expected = ((lo // 16) & 0xffff) | (((hi // 16) & 0xffff) << 16)
        need((scaled, inverted) == (expected, (~expected) & 0xffffffff), 'wrong compiled reference vector')
    return {'elf_sha256': digest, 'kernel': check_elf(path, 'rp_scale_pair'), 'pio_words': [f'{w:04x}' for w in words],
            'pio_descriptor_version': version, 'pio_descriptor_gpio_mask': gpio_mask,
            'verified_compiled_reference_vectors': len(rows), 'hardware_run': False}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('directory', type=Path)
    p.add_argument('--report', action='store_true', help='also write image-check.json inside the build directory')
    a = p.parse_args()
    try:
        result = json.dumps(check(a.directory), indent=2) + '\n'
        if a.report: (a.directory / 'image-check.json').write_text(result, encoding='utf-8')
        print(result, end='')
    except (ImportError, OSError, ValueError, KeyError, struct.error) as exc:
        sys.exit(f'check_image: {exc}')
