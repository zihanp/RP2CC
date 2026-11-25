"""Opt-in Hazard3 ISA selection for one proved whole-kernel shape; never edits portable inputs.

python tools/tune_hazard3.py --source examples/bench.json --generated dist/generated/optimized --out dist/generated/hazard3
Repeat with --verify to replay both portable and target file/receipt checks.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from rp2cc import codegen
from rp2cc.driver import _atomic_write, verify_artifacts
from rp2cc.model import digest, load_json, require

FORMAT='rp2cc.hazard3.tune.v1'
RULE='rv32.sext-srai-srai-pack.floor2.v1'


def sha(b): return hashlib.sha256(b).hexdigest()


def selects_floor2(kernel):
    """Eligibility on an already validated source kernel. No name inference."""
    return len(kernel['args']) == 1 and kernel['body'] == ['floor2', kernel['args'][0]]


def _target_function(fn,arg):
    return '\n'.join([
        f'uint32_t rp_{fn}(uint32_t v_{arg}) {{',
        '#if !defined(__riscv) || !defined(__riscv_xlen) || __riscv_xlen != 32 || !defined(__riscv_zbb) || !defined(__riscv_zbkb)',
        '#error "RP2CC Hazard3 floor2 requires RV32 Zbb and Zbkb; use the portable directory for Arm"',
        '#else',
        '    uint32_t low;',
        '    __asm__ ("sext.h %[lo], %[word]\\n\\t"',
        '             "srai %[lo], %[lo], 4\\n\\t"',
        '             "srai %[word], %[word], 20\\n\\t"',
        '             "pack %[word], %[lo], %[word]"',
        f'             : [word] "+r" (v_{arg}), [lo] "=&r" (low));',
        f'    return v_{arg};', '#endif','}',''])


def _receipt_bytes(c): return (json.dumps(c,sort_keys=True,indent=2)+'\n').encode('ascii')


def plan(source_path, generated):
    """Only after checking the original Python cert and byte-exact regenerated C/H."""
    cert_path=generated/'certificate.json'; core=verify_artifacts(source_path,cert_path)
    require(core['mode']=='optimized','target selector requires checked optimized input, not baseline')
    src=load_json(source_path)
    orig={name:(generated/name).read_bytes() for name in ('kernels.c','kernels.h','certificate.json')}
    require(core['source_sha256']==digest(src) and core==json.loads(orig['certificate.json']), 'source/certificate changed during selection')
    require(codegen.render(src,core)=={name:orig[name] for name in ('kernels.c','kernels.h')}, 'portable bytes changed during selection')
    text=orig['kernels.c'].decode('ascii'); selected=[]
    for sk,ck in zip(src['kernels'],core['kernels']):
        if not selects_floor2(sk): continue
        arg=sk['args'][0]
        # Match the actual target AST and proof pattern; name need not be 'floor2'.
        want=['xor',['add',['and',['lshr',arg,4],0x0fff0fff],0x78007800],0x78007800]
        require(ck['body']==want and ck['steps']==[{'rule':'swar.floor2.bias','path':[],'after':want}],
                f"noncanonical checked floor2 body/steps: {sk['name']}")
        old='\n'.join(codegen._function(ck,set()))
        require(text.count(old)==1,'checked C does not contain one whole expected kernel')
        text=text.replace(old,_target_function(sk['name'],arg),1)
        selected.append({'kernel':sk['name'],'argument':arg,'rule':RULE})
    require(selected,'no eligible whole-kernel floor2(single argument) in this module')
    tuned={'kernels.c':text.encode('ascii'),'kernels.h':orig['kernels.h']}
    receipt={'format':FORMAT,'target':'RV32 Zbb + Zbkb (RP235x Hazard3)','ir_canonical_sha256':digest(src),
             'source_file_sha256':sha(Path(source_path).read_bytes()),'selected':selected,
             'portable_files':{n:sha(v) for n,v in orig.items()},'target_files':{n:sha(v) for n,v in tuned.items()},
             'value_sequence':['sext.h low, word','srai low, low, 4','srai word, word, 20','pack word, low, word'],
             'scope':'portable certificate covers original IR; target receipt checks exact whole-function replacement, not the assembler or the hardware'}
    return {**tuned,**{'portable/'+n:v for n,v in orig.items()},'hazard3.json':_receipt_bytes(receipt)},receipt


def verify(source, generated, out):
    # The saved portable provenance is acceptable for independent replay in a separate checkout;
    # the original generated directory is also independently rechecked when provided.
    expected,receipt=plan(source,generated)
    verify_artifacts(source,out/'portable'/'certificate.json')
    for name,b in expected.items():
        p=out/name
        require(p.is_file() and p.read_bytes()==b,f'target/provenance file differs from replay: {name}')
    require(not (out/'certificate.json').exists(),'refusing a portable certificate attached to target-specific C')
    return receipt


def tune(source, generated, out):
    source,generated,out=Path(source).resolve(),Path(generated).resolve(),Path(out).resolve()
    require(out!=generated,'output must be separate from portable generated directory')
    expected,receipt=plan(source,generated)
    inputs={source,*((generated/n).resolve() for n in ('kernels.c','kernels.h','certificate.json'))}
    require(all((out/n).resolve() not in inputs for n in expected),'target would overwrite source/portable provenance')
    if out.exists() and any(out.iterdir()):
        # Idempotent re-runs are safe. Never overwrite a hand-edited/other output tree.
        return verify(source,generated,out)
    (out/'portable').mkdir(parents=True,exist_ok=True)
    for name,b in expected.items(): _atomic_write(out/name,b)
    return verify(source,generated,out)


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source',type=Path,required=True); ap.add_argument('--generated',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True); ap.add_argument('--verify',action='store_true')
    ns=ap.parse_args(argv)
    try:
        r=verify(ns.source,ns.generated,ns.out) if ns.verify else tune(ns.source,ns.generated,ns.out)
        print('verified Hazard3 selection: '+', '.join('rp_'+k['kernel'] for k in r['selected']))
        return 0
    except (ValueError,OSError,RecursionError) as e:
        print(f'tune_hazard3: {e}',file=sys.stderr); return 2


if __name__=='__main__': raise SystemExit(main())
