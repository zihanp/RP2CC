"""Exercise real parser/emitter/selector subprocesses; stub only SDK/optional Lean boundaries."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import project
from rp2cc.driver import verify_artifacts


class ProjectTests(unittest.TestCase):
    def setUp(self):
        td=tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup); self.d=Path(td.name)
        self.ir=self.d/'input.json'; self.spec=self.d/'example.project.json'
        self.spec.write_text(json.dumps({'version':1,'ir':self.ir.name,'arch':'riscv'}),encoding='utf-8')

    def build(self,body,name,*flags,lean=False):
        self.ir.write_text(json.dumps({'version':1,'kernels':[{'name':'fn','args':['x'],'body':body}]}),encoding='utf-8')
        real_run=subprocess.run; sdk=[]; lean_argv=[]
        def boundary(args,**kw):
            if Path(args[1]).name=='build_firmware.py':
                sdk.append(args); return subprocess.CompletedProcess(args,0)
            if Path(args[1]).name=='check.py':
                lean_argv.append(args); return subprocess.CompletedProcess(args,0)
            return real_run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,**kw)
        out=self.d/name
        with patch.object(project.subprocess,'run',side_effect=boundary), contextlib.redirect_stdout(io.StringIO()):
            project.main([str(self.spec),'--out',str(out),*flags,*([] if lean else ['--python-only'])])
        self.assertEqual(len(sdk),1)
        return out,Path(sdk[0][sdk[0].index('--generated')+1]),lean_argv

    def test_unmatched_riscv_uses_verified_portable(self):
        out,gen,_=self.build(['affine_iter','x',257,1664525,1013904223],'unmatched')
        self.assertEqual(gen,out/'generated'); self.assertFalse((out/'hazard3').exists())
        self.assertEqual(verify_artifacts(self.ir,gen/'certificate.json')['mode'],'optimized')
        # A composite rule can still use the target-independent floor2 simplification.
        out,gen,_=self.build(['add',['floor2','x'],1],'nested')
        self.assertEqual(gen,out/'generated'); self.assertIn(b'0x78007800',(gen/'kernels.c').read_bytes())

    def test_target_selection_portable_override_and_lean_option(self):
        out,gen,lean=self.build(['floor2','x'],'tuned','--kernel-instances',lean=True)
        self.assertEqual(gen,out/'hazard3'); self.assertEqual(len(lean),1); self.assertIn('--kernel-instances',lean[0])
        self.assertIn(b'sext.h',(gen/'kernels.c').read_bytes())
        out,gen,_=self.build(['floor2','x'],'portable','--portable')
        self.assertEqual(gen,out/'generated'); self.assertNotIn(b'sext.h',(gen/'kernels.c').read_bytes())

    def test_invalid_manifest_and_overlapping_output_fail_before_writes(self):
        self.ir.write_text('{}',encoding='utf-8'); out=self.d/'output'
        for raw in ('{"version":1,"ir":"input.json","ir":"other.json"}',
                    '{"version":true,"ir":"input.json"}',
                    '{"version":1,"ir":"input.json","arch":"unknown"}',
                    '{"version":1,"ir":"input.json","sdk_libraries":["a;b"]}',
                    '{"version":1,"ir":"input.json","pio":"source.pio"}'):
            self.spec.write_text(raw,encoding='utf-8')
            with self.subTest(raw=raw), self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                project.main([str(self.spec),'--out',str(out),'--python-only'])
            self.assertFalse(out.exists())
        # The frontend's output ir.json would alias this particular project file.
        rpm=self.d/'math.rpm'; rpm.write_text('def fn(x: u32) -> u32:\n    return x\n',encoding='utf-8')
        spec=self.d/'ir.json'; original=b'{"version":1,"ir":"math.rpm"}'; spec.write_bytes(original)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            project.main([str(spec),'--out',str(self.d),'--python-only'])
        self.assertEqual(spec.read_bytes(),original); self.assertFalse((self.d/'generated').exists())
