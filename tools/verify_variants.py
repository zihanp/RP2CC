"""Offline M33 benchmark audit: independently compile plain C with real -O flags,
compare whole function symbols incl. Thumb literal-pool bytes against measured ELF,
verify its function-pointer dispatch table and recalculate inclusive batch statistics.
No physical board access. Needs pyelftools, capstone and the matching Arm GNU GCC.
"""

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
import shutil
import statistics
import struct
import subprocess
import tempfile

from capstone import Cs, CS_ARCH_ARM, CS_MODE_MCLASS, CS_MODE_THUMB
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

ROOT=Path(__file__).resolve().parents[1]
CASES=('hand_identity','b2_floor2','b3_floor2','bs_floor2','op_floor2','hand_floor2',
       'b2_affine','b3_affine','bs_affine','op_affine','hand_affine')
MODE={'b2':('baseline','O2'),'b3':('baseline','O3'),'bs':('baseline','Os'),'op':('optimized','O3'),'hand':('hand','O3')}
FALLBACK_FLAGS=['-mcpu=cortex-m33','-mthumb','-march=armv8-m.main+fp+dsp','-mcmse','-mfloat-abi=softfp',
                '-ftls-model=local-exec','-std=gnu11','-fno-math-errno','-ffunction-sections','-fdata-sections']


def must(b,msg):
    if not b: raise ValueError(msg)


def sha(b): return hashlib.sha256(b).hexdigest()


def get_symbol(path,name,is_function=True):
    with path.open('rb') as f:
        elf=ELFFile(f)
        must(elf.elfclass==32 and elf.little_endian and elf['e_machine']=='EM_ARM','need LE 32-bit Arm ELF')
        symtab=elf.get_section_by_name('.symtab'); found=[] if symtab is None else (symtab.get_symbol_by_name(name) or [])
        expected='STT_FUNC' if is_function else 'STT_OBJECT'
        found=[s for s in found if s['st_info']['type']==expected and isinstance(s['st_shndx'],int)]
        must(len(found)==1,f'expected unique {expected} symbol: {name}')
        s=found[0]; idx=s['st_shndx']; sec=elf.get_section(idx); address=s['st_value']; value=address&~1 if is_function else address
        off=value-sec['sh_addr']; size=s['st_size']; data=sec.data()
        must(0<size and 0<=off<=len(data)-size,f'invalid {name} symbol range')
        if elf['e_type']=='ET_REL':  # Do not equate unrelocated placeholders to executable bytes.
            for other in elf.iter_sections():
                if isinstance(other,RelocationSection) and other['sh_info']==idx:
                    must(not any(off<=r['r_offset']<off+size for r in other.iter_relocations()),f'unresolved relocation in {name}')
        return address, data[off:off+size]


def options(db):
    if db is None: return FALLBACK_FLAGS
    entries=json.loads(db.read_text(encoding='utf-8'))
    rows=[e for e in entries if Path(e['file']).name=='b2.c']; must(len(rows)==1,'cannot locate baseline TU in compile_commands')
    entry=rows[0]
    if 'arguments' in entry: parts=entry['arguments']
    else: parts=re.findall(r'(?<!\S)(?:-[mfg][^\s\"]+|-std=[^\s\"]+)(?=\s|$)',entry['command'])
    flags=[p for p in parts if p.startswith(('-m','-f','-std=')) and p not in ('-MD','-MMD')]
    must('-mcpu=cortex-m33' in flags and '-mthumb' in flags,'unexpected target flags')
    must(not any(p.startswith('-flto') for p in flags),'replay requires the no-LTO benchmark build')
    return flags


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--bench',type=Path,default=ROOT/'dist/bench/arm'); ap.add_argument('--generated',type=Path,default=ROOT/'dist/generated')
    ap.add_argument('--cc',type=Path); ap.add_argument('--compile-commands',type=Path)
    ap.add_argument('--out',type=Path,default=ROOT/'dist/evidence/variants-arm.json')
    a=ap.parse_args(); run=a.bench; image=run/'image'/'rp_lab.elf'; image_hash=sha(image.read_bytes())
    manifest=json.loads((run/'image'/'manifest.json').read_text(encoding='utf-8')); result=json.loads((run/'results.json').read_text(encoding='utf-8'))
    linked=json.loads((run/'linked_code.json').read_text(encoding='utf-8'))
    must(image_hash==manifest['files']['rp_lab.elf']['sha256']==result['elf_sha256']==linked['sha256'],'not the ELF of the measured run')
    cc=a.cc or next((p for p in (ROOT/'.deps/bin/arm-none-eabi-gcc.exe', ROOT/'.deps/bin/arm-none-eabi-gcc') if p.is_file()),None) or shutil.which('arm-none-eabi-gcc')
    must(cc is not None,'matching cross GCC not found; pass --cc')
    version=subprocess.check_output([str(cc),'--version'],text=True).splitlines()[0]
    must(version.split(' (',1)[-1]==manifest['gcc'].split(' (',1)[-1],'GCC version/build does not match recorded firmware toolchain')
    db=a.compile_commands
    if db is None:
        for candidate in (ROOT/'.build').glob('rp2350-arm-*/compile_commands.json'):
            obj=candidate.with_name('rp_lab.elf')
            if obj.is_file() and sha(obj.read_bytes())==image_hash: db=candidate; break
    flags=options(db)
    report={'format':'rp2cc.variant-audit.v1','target':'Cortex-M33 (Thumb), SDK SRAM benchmark','elf_sha256':image_hash,
            'gcc':version,'flags':flags,'flags_origin':'matching ELF build compile_commands.json' if db else 'explicit published target flags',
            'compare':'plain, no pragma C, using actual command-line -O level; entire ELF symbol inclusive of padding/literals; unresolved relocations rejected',
            'functions':{},'source_sha256':{}}
    with tempfile.TemporaryDirectory(prefix='rp2cc-variants-') as td:
        t=Path(td)
        actual=(ROOT/'bench/hand.c').read_text(encoding='ascii')
        stripped=re.sub(r'^#pragma\s+GCC\s+optimize[^\n]*\n','',actual,flags=re.M)
        (t/'hand.c').write_text(stripped,encoding='ascii')
        # Derive constants for the independent hand control directly at 0 and 1.
        v0,v1=0,1
        for _ in range(257): v0,v1=(v0*1664525+1013904223)&0xffffffff,(v1*1664525+1013904223)&0xffffffff
        (t/'bench_config.h').write_text(f'#define RP_HAND_A UINT32_C({(v1-v0)&0xffffffff})\n#define RP_HAND_B UINT32_C({v0})\n',encoding='ascii')
        for prefix,(mode,level) in MODE.items():
            src=t/'hand.c' if mode=='hand' else a.generated/mode/'kernels.c'
            report['source_sha256'][mode]=sha(src.read_bytes())
            obj=t/f'{prefix}.o'
            subprocess.run([str(cc),*flags,'-'+level,'-DNDEBUG','-c',str(src),'-o',str(obj)],check=True)
            for fn in (n for n in CASES if n.startswith(prefix+'_')):
                base=fn if mode=='hand' else 'rp_'+fn.split('_',1)[1]
                _,plain=get_symbol(obj,base); address,final=get_symbol(image,fn)
                must(final.hex()==linked['symbols'][fn]['bytes_hex'],f'published linked-byte record mismatch: {fn}')
                report['functions'][fn]={'level':'-'+level,'source_mode':mode,'symbol_bytes':len(final),
                         'standalone_hex':plain.hex(),'linked_hex':final.hex(),'linked_address':hex(address&~1),'identical':final==plain}
    # Verify dispatch points to these symbols, not a spare, unmeasured copy in the ELF.
    _,table=get_symbol(image,'funs',False)
    pointers=struct.unpack('<11I',table)
    must(list(pointers)==[get_symbol(image,n)[0] for n in CASES], 'measured function pointer table does not select the audited symbols')
    sample_address,sample=get_symbol(image,'sample'); md=Cs(CS_ARCH_ARM,CS_MODE_THUMB|CS_MODE_MCLASS)
    asm=[]
    for ins in md.disasm(sample,sample_address&~1):
        asm.append(f'{ins.mnemonic} {ins.op_str}'.rstrip())
        if (ins.mnemonic in ('pop','ldm.w','ldmia.w') and 'pc' in ins.op_str) or (ins.mnemonic=='bx' and ins.op_str=='lr'): break
    ind=[op for op in asm if re.fullmatch(r'blx r(?:1[0-2]|[0-9])',op)]
    must(len(ind)==1 and not any(op.startswith('bl ') for op in asm),'expected one shared indirect call, no direct per-variant call')
    report['dispatch']={'ordered_table':list(CASES),'matches_all_thumb_function_pointers':True,'sample_sha256':sha(sample),
                        'common_indirect_call':ind[0],'sample_disassembly':asm}

    raw=(run/'mailbox.bin').read_bytes(); w=struct.unpack('<'+'I'*(len(raw)//4),raw)
    must(w[:6]==(0x31423252,1,result['nonce'],41,128,11) and w[14]==len(w),'raw mailbox identity')
    must(w[11]==0 and w[12]==1024,'on-board differential failure')
    chk=2166136261
    for i,z in enumerate(w[1:],1):
        if i!=15: chk=((chk^z)*16777619)&0xffffffff
    must(chk==w[15],'mailbox checksum mismatch')
    for k,fn in enumerate(CASES):
        values=sorted(w[16+2*(r*11+k)] for r in range(41)); m=statistics.median(values)/128; p95=values[math.ceil(0.95*len(values))-1]/128
        must((m,p95)==(result['summary'][fn]['median_cycles_per_call'],result['summary'][fn]['p95_cycles_per_call']),'cycle summary mismatch')
        report['functions'][fn].update(median_inclusive_cycles=m,p95_inclusive_cycles=p95,
                                       distinct_batch_counts=len(set(values)))
    report['method']={'rounds':41,'calls_per_batch':128,'same_joined_image':True,'clock_hz':w[6],
        'timing':'masked IRQ, warmed SRAM, same shared sample(), same seeds and rotating order, indirect calls in dependency chain, no overhead subtraction',
        'limits':'Single board/code layout and clock; 41 is not a multiple of 11 rotation positions. No XIP/cold-cache/interrupt-pressure or WCET claims. Code size is not just instruction bytes: GCC function symbols may include literal tables.'}
    report['mailbox_sha256']=sha(raw)
    report['all_identical']=all(x['identical'] for x in report['functions'].values())
    a.out.parent.mkdir(parents=True,exist_ok=True); a.out.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    difference=[n for n,x in report['functions'].items() if not x['identical']]
    print(f'{len(report["functions"])-len(difference)}/11 exact standalone vs linked symbols; dispatch and raw cycle summaries verified')
    if difference: print('DIFFERENCES:', ', '.join(difference)); return 1
    return 0


if __name__=='__main__': raise SystemExit(main())
