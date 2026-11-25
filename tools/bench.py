"""Prepare a controlled multi-baseline benchmark, replay raw results, or run our own RAM ELF.

The device operation needs an explicit BOOTSEL serial. Never forces a running device,
never loads a Flash UF2 and never reads the user's firmware. Python >= 3.10.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import statistics
import struct
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
BASE, END, BOX = 0x20000000, 0x20082000, 0x20050000
ROUNDS, INNER, HEADER, MASK = 41, 128, 16, 0xffffffff
CASES = ('hand_identity', 'b2_floor2', 'b3_floor2', 'bs_floor2', 'op_floor2', 'hand_floor2',
         'b2_affine', 'b3_affine', 'bs_affine', 'op_affine', 'hand_affine')
NWORDS = HEADER + 2 * ROUNDS * len(CASES)
BOX_END = BOX + NWORDS*4

def data(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

def prepare(dst, ir, tune='portable', nonce=None):
    if nonce is not None and (type(nonce) is not int or not 0<nonce<=MASK): raise ValueError('nonce must be a nonzero uint32')
    dst=dst.resolve(); dst.mkdir(parents=True, exist_ok=True); extra=[]
    for mode in ('optimized','baseline'):
        subprocess.run([sys.executable,'-m','rp2cc','emit',str(ir.resolve()),'--out',str(dst/mode),'--mode',mode], cwd=ROOT, check=True)
    optimized='optimized'
    if tune=='hazard3':
        subprocess.run([sys.executable,str(ROOT/'tools/tune_hazard3.py'),'--source',str(ir.resolve()),'--generated',str(dst/'optimized'),'--out',str(dst/'hazard3')],cwd=ROOT,check=True)
        optimized='hazard3'
    for prefix, mode, level in [('b2','baseline','O2'),('b3','baseline','O3'),('bs','baseline','Os'),('op',optimized,'O3')]:
        src=dst/mode/'kernels.c'; hdr=dst/mode/'kernels.h'
        names=sorted(set(re.findall(r'\brp_[A-Za-z0-9_]+\b',src.read_text(encoding='utf-8')+hdr.read_text(encoding='utf-8'))))
        if not {'rp_floor2','rp_affine'} <= set(names): raise ValueError('expected bench ABI missing')
        lines=['/* Generated wrapper: same C; only symbol namespace and optimization level change. */',
               '#if !defined(__GNUC__) || defined(__clang__)', '#error Benchmark wrappers require GCC', '#endif',
               f'#pragma GCC optimize ("{level}")']+[f'#define {s} {prefix}_{s[3:]}' for s in names]+[f'#include "{mode}/kernels.c"','']
        unit=dst/f'{prefix}.c'; unit.write_text('\n'.join(lines),encoding='utf-8'); extra.append(str(unit))
    if nonce is None: nonce=secrets.randbits(32) or 1
    # Closed form control calculated independently from f(0) and f(1), by direct iteration.
    y0,y1=0,1
    for _ in range(257): y0,y1=(1664525*y0+1013904223)&MASK,(1664525*y1+1013904223)&MASK
    cfg=dst/'bench_config.h'; cfg.write_text(f'#define RP_RUN_NONCE UINT32_C({nonce})\n#define RP_HAND_A UINT32_C({(y1-y0)&MASK})\n#define RP_HAND_B UINT32_C({y0})\n',encoding='ascii')
    for fname in ('board_bench.c','hand.c'): shutil.copyfile(ROOT/'bench'/fname,dst/fname)
    extra.append(str(dst/'hand.c'))
    data(dst/'plan.json',dict(abi=1,nonce=nonce,input_sha256=hashlib.sha256(ir.read_bytes()).hexdigest(),
         compiler_input=str(ir.resolve()),tune=tune,app=str(dst/'board_bench.c'),generated=str(dst/optimized),extra_sources=extra))
    print('prepared',dst/'plan.json')

def build(planfile,args):
    plan=json.loads(planfile.read_text(encoding='utf-8'))
    if plan.get('tune')=='hazard3' and args.arch!='riscv': raise ValueError('Hazard3 benchmark requires --arch riscv')
    cmd=[sys.executable,str(ROOT/'tools/build_firmware.py'),'--generated',plan['generated'],'--board','rp2350','--arch',args.arch,'--ram','--app',plan['app'],'--out',str(args.out.resolve()),'-j',str(args.jobs)]
    for src in plan['extra_sources']: cmd+=['--extra-source',src]
    if args.transport=='cdc': cmd+=['--usb-stdio']
    cmd+=args.build_args; print('building SRAM benchmark:',subprocess.list2cmdline(cmd),flush=True); subprocess.run(cmd,check=True)

def inspect_image(path):
    from elftools.elf.elffile import ELFFile
    from elftools.elf.sections import SymbolTableSection
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_MCLASS, CS_ARCH_RISCV, CS_MODE_RISCV32, CS_MODE_RISCVC
    out={}; segs=[]
    with path.open('rb') as f:
        elf=ELFFile(f)
        if not elf.little_endian or elf.elfclass!=32 or elf['e_machine'] not in ('EM_ARM','EM_RISCV') or elf['e_type']!='ET_EXEC': raise ValueError('expected linked RP2350 32-bit LE image')
        arm=elf['e_machine']=='EM_ARM'
        for s in elf.iter_segments():
            if s['p_type']!='PT_LOAD' or not s['p_memsz']: continue
            a=s['p_paddr']; v=s['p_vaddr']; n=s['p_memsz']; size=s['p_filesz']
            for q in (a,v):
                if not (BASE<=q<END and size<=n<=END-q): raise ValueError('not an exclusively SRAM image')
                if q<BOX_END and q+n>BOX: raise ValueError('SRAM mailbox overlaps linked image')
            segs.append(dict(paddr=hex(a),vaddr=hex(v),memsz=n,filesz=size))
        if not segs or not BASE <= (elf['e_entry'] & ~1) < END: raise ValueError('bad image/entry')
        md=Cs(CS_ARCH_ARM, CS_MODE_MCLASS|CS_MODE_THUMB) if arm else Cs(CS_ARCH_RISCV,CS_MODE_RISCV32|CS_MODE_RISCVC)
        for table in elf.iter_sections():
            if not isinstance(table,SymbolTableSection): continue
            for sym in table.iter_symbols():
                if sym.name not in CASES or sym['st_info']['type']!='STT_FUNC': continue
                sec=elf.get_section(sym['st_shndx']); va=sym['st_value']&~1; off=va-sec['sh_addr']; size=sym['st_size']
                bs=sec.data()[off:off+size]; ins=[]
                for i in md.disasm(bs,va):
                    ins.append(f'{i.address:08x}  {i.bytes.hex():8}  {i.mnemonic} {i.op_str}'.rstrip())
                    # GCC places literal pools after bx lr; don't represent literals as instructions.
                    if (i.mnemonic=='bx' and i.op_str=='lr') or i.mnemonic=='ret' or (i.mnemonic=='c.jr' and i.op_str=='ra'): break
                out[sym.name]=dict(address=hex(va),symbol_bytes=size,bytes_hex=bs.hex(),disassembly=ins,disassembly_source='capstone (prefix through return or first unsupported instruction)')
    if set(out)!=set(CASES): raise ValueError('not the expected multi-case benchmark ELF')
    # Object dumps from the matching target toolchain include Hazard3 bitmanip opcodes
    # and label Thumb literal pools. Accept text only when ALL encoded bytes match ELF.
    listing=path.with_suffix('.dis')
    if listing.is_file():
        parsed={k:[] for k in out}; current=None
        for line in listing.read_text(encoding='utf-8',errors='replace').splitlines():
            label=re.fullmatch(r'\s*[\da-fA-F]+ <([^>]+)>:\s*',line)
            if label:
                current=label[1] if label[1] in parsed else None
                continue
            if not current: continue
            row=re.match(r'^\s*([\da-fA-F]+):\s+((?:[\da-fA-F]{4,8}[ \t]+)+)(\S.*)$',line)
            if not row: continue
            addr=int(row[1],16); raw=b''.join(bytes.fromhex(t)[::-1] for t in row[2].split())
            base=int(out[current]['address'],16); size=out[current]['symbol_bytes']
            if base<=addr and addr+len(raw)<=base+size:
                parsed[current].append((addr,raw,' '.join(row[3].split())))
        for k,rows in parsed.items():
            actual=bytes.fromhex(out[k]['bytes_hex']); start=int(out[k]['address'],16); cursor=start
            for addr,raw,_ in rows:
                if addr!=cursor or raw!=actual[cursor-start:cursor-start+len(raw)]:
                    raise ValueError('disassembly/ELF byte mismatch: '+k)
                cursor+=len(raw)
            if cursor!=start+len(actual): raise ValueError('incomplete companion disassembly: '+k)
            out[k]['disassembly']=[f'{addr:08x}  {raw.hex():8}  {asm}' for addr,raw,asm in rows]
            out[k]['disassembly_source']='target GNU objdump; every byte verified against linked ELF, includes explicit data directives'
    return dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),arch='arm' if arm else 'riscv',segments=segs,symbols=out)

def expected(round_no):
    x0=0x1234abcd ^ ((0x9e3779b9*(round_no+1))&MASK)
    xi=xf=xa=x0
    def signed(u): return (u&32767)-(u&32768)
    for i in range(INNER):
        k=(0x9e3779b9+i)&MASK; xi^=k; v=xf^k
        xf=((signed(v)//16)&65535)|(((signed(v>>16)//16)&65535)<<16)
        xa^=k
        for _ in range(257): xa=(1664525*xa+1013904223)&MASK
    return [xi]+[xf]*5+[xa]*5

def decode(blob, nonce):
    if len(blob)!=NWORDS*4: raise ValueError('incorrect mailbox length')
    w=struct.unpack(f'<{NWORDS}I',blob)
    if w[:6]!=(0x31423252,1,nonce,ROUNDS,INNER,len(CASES)) or w[13] not in (1,2) or w[14]!=len(w): raise ValueError('mailbox signature, nonce or dimensions differ')
    h=2166136261
    for i,z in enumerate(w[1:],1):
        if i!=15: h=((h^z)*16777619)&MASK
    if h!=w[15] or w[11]!=0 or w[12]!=1024 or not w[6]: raise ValueError('invalid checksum, device differential test or clock')
    samples={k:[] for k in CASES}; digests=[]
    for r in range(ROUNDS):
        want=expected(r); got=[]
        for i,k in enumerate(CASES):
            n=HEADER+2*(r*len(CASES)+i); cycles,chk=w[n:n+2]
            if not (0<cycles<0x7fffff) or chk!=want[i]: raise ValueError(f'invalid timing or checksum, round {r} {k}')
            samples[k].append(cycles); got.append(chk)
        digests.append(got)
    stats={}
    for k,vals in samples.items():
        q=sorted(vals); stats[k]={'median_cycles_per_call':statistics.median(q)/INNER,
             'p95_cycles_per_call':q[math.ceil(.95*len(q))-1]/INNER,'min_batch_cycles':q[0],'max_batch_cycles':q[-1]}
    return dict(abi=1,nonce=nonce,iterations_per_sample=INNER,rounds=ROUNDS,clock_hz=w[6],io_pass_mask=w[7],
        sysinfo_chip_id=hex(w[8]),package_sel=w[9],cpu_id_register='CPUID' if w[13]==1 else 'mimpid',cpu_id_value=hex(w[10]),device_vectors_per_case=w[12],
        device_mismatches=w[11],host_verified_sample_digests=ROUNDS*len(CASES),counter=('Arm SysTick' if w[13]==1 else 'Hazard3 mcycle')+', processor clock, interrupts masked, SRAM',
        cycles_include='batch loop, indirect call, dependency, timer edges; no overhead subtraction',
        summary=stats,raw_batch_cycles=samples,raw_output_digests=digests)

def run(a):
    plan=json.loads(a.plan.read_text(encoding='utf-8')); image=inspect_image(a.elf)
    if not re.fullmatch(r'[a-fA-F0-9]{16}',a.serial): raise ValueError('expected a BOOTSEL serial (16 hex digits)')
    pt=str(a.picotool.resolve()); sel=['--ser',a.serial]; out=a.out.resolve(); out.mkdir(parents=True,exist_ok=True)
    def ctl(*items,check=True):
        p=subprocess.run([pt,*map(str,items),*sel],capture_output=True,text=True,timeout=20)
        if check and p.returncode: raise RuntimeError(p.stdout+p.stderr)
        return p
    meta=ctl('info','-d').stdout
    if not re.search(r'type:\s+RP2350',meta): raise ValueError('not the requested RP2350 family')
    # Only our known-built image and release with matching digest are accepted for execution.
    if image['sha256'].lower()!=a.sha256.lower(): raise ValueError('explicit ELF hash mismatch')
    if a.transport=='cdc':
        from usb_capture import capture, ports_snapshot
        before=ports_snapshot()
    print('Loading verified SRAM segments only; auto-return to BOOTSEL.',flush=True)
    loaded=ctl('load','-v','-x',a.elf.resolve()); print(loaded.stdout,flush=True)
    deadline=time.monotonic()+30; err='no response'
    if a.transport=='cdc':
        b=capture(plan['nonce'],timeout=30,before=before,expected_length=NWORDS*4)
        result=decode(b,plan['nonce']); (out/'mailbox.bin').write_bytes(b)
        if (result['cpu_id_register']=='CPUID')!=(image['arch']=='arm'): raise ValueError('wrong ISA for returned capture')
        while time.monotonic()<deadline:
            if ctl('info','-d',check=False).returncode==0: break
            time.sleep(.3)
        else: raise RuntimeError('got CDC result; board not yet seen back in BOOTSEL')
    else:
      with tempfile.TemporaryDirectory() as td:
        tmp=Path(td)/'capture.bin'
        while time.monotonic()<deadline:
            time.sleep(.5)
            p=ctl('save','-r',hex(BOX),hex(BOX_END),str(tmp),'-t','bin',check=False)
            if p.returncode: err=(p.stdout+p.stderr).strip(); continue
            b=tmp.read_bytes()
            try:
                result=decode(b,plan['nonce'])
                if (result['cpu_id_register']=='CPUID')!=(image['arch']=='arm'): raise ValueError('wrong ISA for returned capture')
            except ValueError as ex: err=str(ex); continue
            (out/'mailbox.bin').write_bytes(b); break
        else: raise RuntimeError(f'No matching return mailbox. No force/reset was issued. Last: {err}')
    result.update(elf_sha256=image['sha256'],input_sha256=plan['input_sha256'],picotool=subprocess.check_output([pt,'version'],text=True).strip(),
                  board_info=re.sub(r'(?im)^\s*chipid:.*\n','\n',meta).strip(),board_after='BOOTSEL',transport=a.transport,
                  host_capture_time=time.strftime('%Y-%m-%dT%H:%M:%S%z'),load_verify=loaded.stdout.strip())
    data(out/'results.json',result);data(out/'linked_code.json',image)
    print('io mask:',hex(result['io_pass_mask']), 'median inclusive cycles/call:',{k:v['median_cycles_per_call'] for k,v in result['summary'].items()})

def main():
    p=argparse.ArgumentParser(description=__doc__); s=p.add_subparsers(dest='op',required=True)
    t=s.add_parser('prepare');t.add_argument('--out',type=Path,required=True);t.add_argument('--input',type=Path,default=ROOT/'examples/bench.json');t.add_argument('--tune',choices=['portable','hazard3'],default='portable');t.add_argument('--nonce',type=lambda x:int(x,0),help='offline reconstruction only: use recorded nonzero run ID; omit on the board for a fresh ID')
    t=s.add_parser('build');t.add_argument('--plan',type=Path,required=True);t.add_argument('--out',type=Path,required=True);t.add_argument('--arch',choices=['arm','riscv'],default='arm');t.add_argument('--transport',choices=['cdc','mailbox'],default='cdc');t.add_argument('-j','--jobs',type=int,default=5);t.add_argument('build_args',nargs='*')
    t=s.add_parser('inspect');t.add_argument('--elf',type=Path,required=True);t.add_argument('--out',type=Path)
    t=s.add_parser('run');t.add_argument('--plan',type=Path,required=True);t.add_argument('--elf',type=Path,required=True);t.add_argument('--sha256',required=True);t.add_argument('--picotool',type=Path,required=True);t.add_argument('--serial',required=True);t.add_argument('--out',type=Path,required=True);t.add_argument('--transport',choices=['cdc','mailbox'],default='cdc')
    t=s.add_parser('replay');t.add_argument('--results',type=Path,required=True);t.add_argument('--mailbox',type=Path,required=True)
    a=p.parse_args()
    if a.op=='prepare': prepare(a.out,a.input,a.tune,a.nonce)
    elif a.op=='build': build(a.plan,a)
    elif a.op=='inspect':
        image=inspect_image(a.elf)
        if a.out: data(a.out,image)
        else: print(json.dumps(image,indent=2))
    elif a.op=='run': run(a)
    else:
        saved=json.loads(a.results.read_text(encoding='utf-8')); replay=decode(a.mailbox.read_bytes(),saved['nonce'])
        if any(saved.get(k)!=v for k,v in replay.items()): raise ValueError('published report differs from mailbox')
        print(f"verified {replay['host_verified_sample_digests']} host/board digests and raw timing records")

if __name__=='__main__': main()
