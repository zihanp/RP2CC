"""Build an identical XIP client twice and measure its linked flash address span, not ELF file length."""
import argparse, hashlib, json, subprocess, sys
from pathlib import Path
from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection

ROOT=Path(__file__).resolve().parents[1]
def measure(path):
    with path.open('rb') as f:
        elf=ELFFile(f); spans=[]; syms={}
        for seg in elf.iter_segments():
            if seg['p_type']=='PT_LOAD' and seg['p_filesz'] and 0x10000000<=seg['p_paddr']<0x11000000:
                spans.append((seg['p_paddr'],seg['p_paddr']+seg['p_filesz']))
        for tab in elf.iter_sections():
            if isinstance(tab,SymbolTableSection):
                for s in tab.iter_symbols():
                    if s.name=='rp2cc_pool' or s.name.startswith('rp_rom_'):
                        syms[s.name]=dict(address=hex(s['st_value']),size=s['st_size'])
    if not spans or 'rp2cc_pool' not in syms or sum(n.startswith('rp_rom_') for n in syms)<6:
        raise ValueError('pool or live ROM getters missing in XIP ELF')
    return dict(flash_span_bytes=max(b for _,b in spans)-min(a for a,_ in spans),
                flash_start=hex(min(a for a,_ in spans)),segments=[[hex(a),hex(b)] for a,b in spans],
                symbols=syms,elf_sha256=hashlib.sha256(path.read_bytes()).hexdigest())

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--out',type=Path,required=True)
    p.add_argument('--generated',type=Path,default=ROOT/'dist/generated');p.add_argument('--board',default='rp2354a')
    p.add_argument('--skip-build',action='store_true');a=p.parse_args();out=a.out.resolve();out.mkdir(parents=True,exist_ok=True)
    sizes={}
    for mode in ('baseline','optimized'):
        d=out/mode
        if not a.skip_build:
            subprocess.run([sys.executable,str(ROOT/'tools/build_firmware.py'),'--generated',str(a.generated.resolve()/mode),
                            '--app',str(ROOT/'bench/rom_footprint.c'),'--board',a.board,'--arch','arm','--opt','O3',
                            '--out',str(d)],check=True)
        files=list(d.glob('*.elf'))
        if len(files)!=1: raise ValueError(f'exactly one ELF is needed in {d}')
        sizes[mode]=measure(files[0]);sizes[mode]['elf']=files[0].name
    record=dict(board=a.board,mode='Arm XIP / GCC -O3, identical app, no board write',source_sha256=hashlib.sha256((ROOT/'bench/rom_footprint.c').read_bytes()).hexdigest(),
                images=sizes,flash_bytes_saved=sizes['baseline']['flash_span_bytes']-sizes['optimized']['flash_span_bytes'])
    (out/'comparison.json').write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8');print(json.dumps(record,indent=2))

if __name__=='__main__': main()
