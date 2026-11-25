"""Check an actual ELF leaf against the exact RV32 value/ABI sequence modeled in Lean.
This decoder is independent of the C template, GCC, and objdump disassembly text.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from rp2cc.model import fields, identifier, load_json


def require(b,m):
    if not b: raise ValueError(m)


def check_words(data):
    code=[]; i=0
    while i<len(data):
        require(i+2<=len(data),'truncated instruction')
        half=int.from_bytes(data[i:i+2],'little'); n=4 if half&3==3 else 2
        require(i+n<=len(data),'truncated instruction')
        code.append((n,int.from_bytes(data[i:i+n],'little'))); i+=n
    require(len(code)==5,'expected exactly five instructions, including return')
    n,w=code[0]; tmp=(w>>7)&31
    require(n==4 and (w&0xfff0707f)==0x60501013 and ((w>>15)&31)==10, 'expected sext.h tmp,a0')
    require(tmp in {5,6,7,11,12,13,14,15,16,17,28,29,30,31},'temporary must be a distinct, caller-saved GPR (not ra)')
    def srai(inst,r,count):
        n,w=inst
        if n==2:
            return w&3==1 and (w>>13)==4 and ((w>>12)&1)==0 and ((w>>10)&3)==1 and 8+((w>>7)&7)==r and ((w>>2)&31)==count
        return w&0xfe00707f==0x40005013 and ((w>>7)&31)==r and ((w>>15)&31)==r and ((w>>20)&31)==count
    require(srai(code[1],tmp,4),'expected (c.)srai tmp,4')
    require(srai(code[2],10,20),'expected (c.)srai a0,20')
    n,w=code[3]
    require(n==4 and w&0xfe00707f==0x08004033 and ((w>>7)&31)==10 and ((w>>15)&31)==tmp and ((w>>20)&31)==10,
            'expected pack a0,tmp,a0')
    require(code[4] in ((2,0x8082),(4,0x00008067)),'expected ret through ra')
    return {'bytes':len(data),'hex':data.hex(),'temp_gpr':tmp,'arg_and_result_gpr':10,
            'sequence':['sext.h tmp,a0','srai tmp,tmp,4','srai a0,a0,20','pack a0,tmp,a0','ret'],
            'additional_memory_branches_or_spills':False}


def check_elf(path,name):
    from elftools.common.exceptions import ELFError
    from elftools.elf.elffile import ELFFile
    try:
        with path.open('rb') as f: return _elf_symbol(ELFFile(f),name)
    except (ELFError,struct.error) as exc: raise ValueError(f'invalid ELF: {exc}') from exc


def _elf_symbol(e,name):
    from elftools.elf.relocation import RelocationSection
    require(e['e_machine']=='EM_RISCV' and e.elfclass==32 and e.little_endian and e['e_type'] in ('ET_REL','ET_EXEC'),'need RV32 LE object or executable')
    tab=e.get_section_by_name('.symtab'); syms=[] if tab is None else tab.get_symbol_by_name(name) or []
    syms=[s for s in syms if s['st_info']['type']=='STT_FUNC' and isinstance(s['st_shndx'],int)]
    require(len(syms)==1,'missing or ambiguous ELF function: '+name)
    s=syms[0]; idx=s['st_shndx']; sec=e.get_section(idx); addr=s['st_value']; size=s['st_size']
    off=addr-sec['sh_addr']; end=off+size
    require(sec['sh_type']=='SHT_PROGBITS' and sec['sh_flags']&6==6 and not sec['sh_flags']&0x800 and
            addr%2==0 and size in (14,16,18,20) and 0<=off<end<=sec['sh_size'],
            'expected allocated, executable and aligned ELF function of the selected size')
    if e['e_type']=='ET_EXEC':
        # The exact file bytes audited must also be the ones actually mapped to this
        # runtime address by an executable load segment (not merely a symbol/debug view).
        require(any(p['p_type']=='PT_LOAD' and p['p_flags']&1 and p['p_vaddr']<=addr and
                    addr+size<=p['p_vaddr']+p['p_filesz'] and
                    p['p_offset']+addr-p['p_vaddr']==sec['sh_offset']+off for p in e.iter_segments()),
                'symbol bytes not mapped by an executable ELF load segment')
    for x in e.iter_sections():
        if isinstance(x,RelocationSection) and x['sh_info']==idx:
            base=off if e['e_type']=='ET_REL' else addr
            require(not any(base<=r['r_offset']<base+size for r in x.iter_relocations()),'relocation inside selected sequence')
    e.stream.seek(sec['sh_offset']+off); raw=e.stream.read(size)
    require(len(raw)==size,'truncated selected ELF function bytes')
    return check_words(raw)


def selected_names(receipt):
    fields(receipt,('format','target','ir_canonical_sha256','source_file_sha256','selected','portable_files','target_files','value_sequence','scope'))
    require(receipt['format']=='rp2cc.hazard3.tune.v1','unknown receipt')
    require(receipt['target']=='RV32 Zbb + Zbkb (RP235x Hazard3)' and
            receipt['value_sequence']==['sext.h low, word','srai low, low, 4','srai word, word, 20','pack word, low, word'],
            'unexpected target or value sequence')
    fields(receipt['portable_files'],('kernels.c','kernels.h','certificate.json'))
    fields(receipt['target_files'],('kernels.c','kernels.h'))
    for h in (receipt['ir_canonical_sha256'],receipt['source_file_sha256'],*receipt['portable_files'].values(),*receipt['target_files'].values()):
        require(type(h) is str and len(h)==64 and all(c in '0123456789abcdef' for c in h),'invalid receipt hash')
    chosen=receipt['selected']
    require(type(chosen) is list and 1<=len(chosen)<=128,'need 1..128 selected kernels')
    names=[]
    for entry in chosen:
        fields(entry,('kernel','argument','rule'))
        require(identifier(entry['kernel']) and identifier(entry['argument']) and entry['kernel'] not in names,
                'duplicate or invalid selected kernel/argument')
        require(entry['rule']=='rv32.sext-srai-srai-pack.floor2.v1','unknown selected rule')
        names.append(entry['kernel'])
    return names


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('--elf',type=Path,required=True)
    ap.add_argument('--receipt',type=Path,required=True); ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--prefix',default='rp_',help='actual final ELF symbol prefix (e.g. op_ for a benchmark wrapper)')
    ns=ap.parse_args(argv)
    try:
        require(ns.out.resolve() not in (ns.elf.resolve(),ns.receipt.resolve()),'output must not overwrite ELF or receipt')
        r=load_json(ns.receipt,256*1024)
        symbols={ns.prefix+n:check_elf(ns.elf,ns.prefix+n) for n in selected_names(r)}
        result={'format':'rp2cc.hazard3.binary-audit.v1','elf_sha256':hashlib.sha256(ns.elf.read_bytes()).hexdigest(),
                'receipt_sha256':hashlib.sha256(ns.receipt.read_bytes()).hexdigest(),'elf_symbols':symbols}
        ns.out.parent.mkdir(parents=True,exist_ok=True); ns.out.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
        print(f'verified {len(symbols)} RV32 leaf symbol(s): '+', '.join(f'{n} ({v["bytes"]} bytes)' for n,v in symbols.items())); return 0
    except (OSError,ValueError,KeyError,ImportError) as e:
        print(f'check_hazard3_code: {e}',file=sys.stderr);return 2


if __name__=='__main__': raise SystemExit(main())
