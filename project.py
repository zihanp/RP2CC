"""Build a project from math kernels plus a trusted SDK application; never programs a board."""
from pathlib import Path
import argparse,re,subprocess,sys

from rp2cc.model import load_json
from tools.build_firmware import FAMILY, PROFILES
from tools.tune_hazard3 import selects_floor2

ROOT=Path(__file__).resolve().parent
def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('project',type=Path);p.add_argument('--out',required=True,type=Path)
    p.add_argument('--mode',choices=['baseline','optimized'],default='optimized');p.add_argument('--board');p.add_argument('--arch',choices=['arm','riscv']);p.add_argument('--ram',action='store_true')
    p.add_argument('--lean',type=Path,help='explicit Lean 4.33.1 executable');p.add_argument('--python-only',action='store_true',help='skip the additional Lean build/replay');p.add_argument('--kernel-instances',action='store_true',help='also elaborate one concrete universal Lean theorem per kernel and per table (best for small programs)');p.add_argument('--portable',action='store_true',help='use generic lowering even on Hazard3');a=p.parse_args(argv)
    if a.python_only and a.kernel_instances:p.error('--kernel-instances requires Lean')
    spec=a.project.resolve()
    try: v=load_json(spec)
    except (ValueError,OSError) as exc:p.error(str(exc))
    allowed={'version','ir','app','board','arch','sdk_libraries','pio','sources','usb_stdio'}
    if type(v) is not dict or set(v)-allowed or type(v.get('version')) is not int or v['version']!=1 or not isinstance(v.get('ir'),str): p.error('invalid project v1: expected ir and known fields')
    inputs={spec}
    def local(s):
        if type(s) is not str: p.error('expected filename string')
        path=(spec.parent/s).resolve()
        if not path.is_file(): p.error(f'missing source {path}')
        if ';' in str(path): p.error('CMake list separator is not allowed in source paths')
        inputs.add(path)
        return str(path)
    for key in ('sources','pio','sdk_libraries'):
        if not isinstance(v.get(key,[]),list) or any(type(x) is not str for x in v.get(key,[])):p.error(f'expected {key} string array')
    for field in ('app','board','arch'):
        if field in v and type(v[field]) is not str: p.error(f'expected string: {field}')
    target_board=a.board or v.get('board','rp2350'); target_arch=a.arch or v.get('arch','arm')
    if target_board not in PROFILES or target_arch not in FAMILY:p.error('unknown board/architecture for this SDK project')
    if any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*',n) for n in v.get('sdk_libraries',[])):p.error('invalid SDK library target')
    ir=local(v['ir']); extra=[]
    for file in v.get('sources',[]): extra+=['--extra-source',local(file)]
    for file in v.get('pio',[]): extra+=['--pio',local(file)]
    for lib in v.get('sdk_libraries',[]): extra+=['--sdk-lib',lib]
    if 'app' in v: extra+=['--app',local(v['app'])]
    if type(v.get('usb_stdio',False)) is not bool:p.error('usb_stdio must be true or false')
    if v.get('usb_stdio'):extra+=['--usb-stdio']
    if a.ram: extra.append('--ram')
    out=a.out.resolve()
    if (out.exists() and not out.is_dir()) or ';' in str(out):p.error('invalid project output directory')
    if sys.platform=='win32' and (Path(out.anchor)/'INFO_UF2.TXT').is_file():p.error('output cannot be a BOOTSEL drive')
    gens=out/'generated'; fw=out/'firmware'
    targets=[gens/f for f in ('kernels.c','kernels.h','certificate.json','stats.json')]
    targets += [fw/f for f in ('rp_lab.elf','rp_lab.uf2','rp_lab.bin','rp_lab.elf.map','rp_lab.dis','manifest.json','build.log')]
    if not a.python_only: targets.append(out/'lean.json')
    if Path(ir).suffix=='.rpm': targets += [out/'ir.json',out/'frontend.json']
    if target_arch=='riscv' and a.mode=='optimized' and not a.portable:
        targets += [out/'hazard3'/f for f in ('kernels.c','kernels.h','hazard3.json','portable/kernels.c','portable/kernels.h','portable/certificate.json')]
    if any(t.resolve() in inputs for t in targets):p.error('an output would overwrite a project input; choose another output directory')
    def call(*args):subprocess.run([sys.executable,*map(str,args)],cwd=ROOT,check=True)
    if Path(ir).suffix=='.rpm':
        call(ROOT/'tools/math_frontend.py',ir,'--out',out/'ir.json','--trace',out/'frontend.json'); ir=str(out/'ir.json')
    call('-m','rp2cc','emit',ir,'--mode',a.mode,'--out',gens)
    call('-m','rp2cc','verify',ir,'--cert',gens/'certificate.json')
    if not a.python_only:
        call(ROOT/'formal/check.py','--source',ir,'--cert',gens/'certificate.json','--report',out/'lean.json',*(['--lean',a.lean.resolve()] if a.lean else []),*(['--kernel-instances'] if a.kernel_instances else []))
    if target_arch=='riscv' and a.mode=='optimized' and not a.portable:
        if any(selects_floor2(k) for k in load_json(ir)['kernels']):
            profile=out/'hazard3'; call(ROOT/'tools/tune_hazard3.py','--source',ir,'--generated',gens,'--out',profile); gens=profile
        else: print('No whole-kernel Hazard3 pattern matched; using the verified portable C.',flush=True)
    call(ROOT/'tools/build_firmware.py','--generated',gens,'--board',target_board,
         '--arch',target_arch,'--out',fw,*extra)
    print(f'Generated and checked locally: {fw}')
if __name__=='__main__':
    try: main()
    except (ValueError,OSError,subprocess.CalledProcessError) as exc:
        print(f'project: {exc}',file=sys.stderr); sys.exit(2)
