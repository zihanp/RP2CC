import copy
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from rp2cc import checker, driver, propose
from rp2cc.model import Invalid, MASK, blobs_of, digest, evaluate, load_json, validate

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "examples" / "bench.json"


def kernel(body, args=("x",), known=None):
    return {"name": "k", "args": list(args), "known": known or {}, "body": body}


def program(body, args=("x",), known=None):
    return {"version": 1, "kernels": [kernel(body, args, known)]}


class CompilerTests(unittest.TestCase):
    def test_example_sources(self):
        s = load_json(BENCH)
        a, _, st = driver.compile_source(s)
        self.assertEqual(st["rules"], {"affine.jump": 1, "cancel.u32": 1, "fold.u32": 1, "swar.floor2.bias": 1})
        self.assertEqual((st["baseline_pool_bytes"], st["pool_bytes"]), (148, 84))
        self.assertEqual((st['pool_lower_bound_bytes'], st['pool_distinct_octets'], st['pool_alignment_length_gcd_bytes'], st['pool_optimal']), (84, 83, 4, True))
        self.assertEqual(a["kernels"][1]["body"], ["add", ["mul", "x", 0x46a65a0d], 0xe89f545f])
        checker.check_cert(s, a)
        self.assertEqual(a, driver.compile_source(s)[0])

    def test_affine_boundaries_and_random(self):
        rng = random.Random(2354)
        cases = [(n, a, b) for n in (0, 1, 2, 3, 17, 256, 257, 65535)
                 for a, b in ((0, MASK), (1, MASK), (MASK, 0), (1664525, 1013904223))]
        cases += [(rng.randrange(300), rng.getrandbits(32), rng.getrandbits(32)) for _ in range(64)]
        for n, a, b in cases:
            body = ["affine_iter", "x", n, a, b]
            cert, _, st = driver.compile_source(program(body))
            self.assertFalse(st["fallback"])
            for x in (0, MASK, rng.getrandbits(32)):
                env = {"x": x}
                self.assertEqual(evaluate(body, env), evaluate(cert["kernels"][0]["body"], env))

    def test_composed_operators(self):
        rng = random.Random(0x2350)
        def expr(depth):
            op = rng.choice(["add", "sub", "xor", "and", "or", "mul", "not", "shl", "lshr", "floor2", "affine_iter"])
            if depth == 0:
                return rng.choice(["x", "y", "scale", 0, 1, MASK, rng.getrandbits(32)])
            a = expr(depth - 1)
            if op in ("not", "floor2"): return [op, a]
            if op in ("shl", "lshr"): return [op, a, rng.randrange(32)]
            if op == "affine_iter": return [op, a, rng.randrange(32), rng.getrandbits(32), rng.getrandbits(32)]
            return [op, a, expr(depth - 1)]
        for _ in range(150):
            body = expr(3)
            shape = program(body, ("x", "y"), {"scale": 7})
            cert, _, stats = driver.compile_source(shape)
            self.assertFalse(stats["fallback"])
            for _ in range(12):
                env = {"x": rng.getrandbits(32), "y": rng.getrandbits(32), "scale": 7}
                self.assertEqual(evaluate(body, env), evaluate(cert["kernels"][0]["body"], env))

    def test_named_simplifications(self):
        for op, a, result in (('add', 0, 'x'), ('sub', 0, 'x'), ('mul', 1, 'x'), ('and', MASK, 'x'),
                              ('xor', 0, 'x'), ('or', 0, 'x'), ('and', 0, 0), ('mul', 0, 0),
                              ('or', MASK, MASK), ('shl', 0, 'x'), ('lshr', 0, 'x')):
            for e in [[op, 'x', a]] + ([[op, a, 'x']] if op not in ('sub','shl','lshr') else []):
                self.assertEqual(driver.compile_source(program(e))[0]['kernels'][0]['body'], result)
        for e, result in ((['sub','x','x'], 0), (['xor','x','x'], 0), (['and','x','x'],'x'),
                          (['or','x','x'],'x'), (['not',['not','x']],'x'),
                          (['add',['sub','x','y'],'y'],'x'), (['sub',['add','x','y'],'y'],'x')):
            self.assertEqual(driver.compile_source(program(e, ('x','y')))[0]['kernels'][0]['body'], result)

    def test_detects_corrupt_proofs_and_codegen(self):
        src = load_json(BENCH)
        cert, _, _ = driver.compile_source(src)
        for transform in (
            lambda c: c.update(source_sha256="0" * 64), lambda c: c.update(mode="baseline"),
            lambda c: c["kernels"][0].update(body=0), lambda c: c["kernels"][0].update(name="affine"),
            lambda c: c["kernels"][1]["steps"][0].update(rule="assume.correct"),
            lambda c: c["kernels"][1]["steps"][0].update(path=[0]),
            lambda c: c["kernels"][1]["steps"][0].update(after=False),
            lambda c: c["kernels"][1]["steps"][0]["after"].__setitem__(2, 5),
            lambda c: c["pool"]["offsets"].update(middle=1),
            lambda c: c["pool"]["offsets"].update(ramp=32),
            lambda c: c["pool"]["offsets"].pop("empty"),
            lambda c: c["pool"].update(bytes="00" + c["pool"]["bytes"][2:4] + "ff" + c["pool"]["bytes"][6:]),
            lambda c: c["pool"].update(bytes=c["pool"]["bytes"] + "00" * 70),
        ):
            bad = copy.deepcopy(cert)
            transform(bad)
            with self.assertRaises(ValueError): checker.check_cert(src, bad)
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp); driver.write_artifacts(BENCH, d, 'optimized')
            driver.verify_artifacts(BENCH, d / 'certificate.json')
            with (d / 'kernels.c').open('ab') as f: f.write(b'/* changed */')
            with self.assertRaisesRegex(Invalid, 'differs'): driver.verify_artifacts(BENCH, d / 'certificate.json')
            driver.write_artifacts(BENCH, d, 'baseline')
            p = d / 'certificate.json'; c = load_json(p); c['files']['kernels.h'] = '1' * 64
            p.write_text(json.dumps(c), encoding='utf-8')
            with self.assertRaisesRegex(Invalid, 'hash'): driver.verify_artifacts(BENCH, p)

    def test_validation_is_fail_closed(self):
        for body in (["mmio.write", 0, 1], ["irq", 3], ["rdcycle"], ["shl", "x", 32],
                     ["lshr", "x", "x"], ["affine_iter", "x", 65536, 1, 2],
                     ["affine_iter", "x", 1, "x", 2], True, 0.0, -1, 1 << 32, ["or", "z", 0]):
            with self.assertRaises(Invalid): validate(program(body))
        for args, known in ((["x", "x"], {}), (["x"], {"x": 1}), (["bad){}"], {})):
            with self.assertRaises(Invalid): validate(program(0, args, known))
        for obj in ({'name':'x','hex':'000','align':1}, {'name':'x','hex':'00','align':3}):
            with self.assertRaises(Invalid): validate({**program(0), 'blobs':[obj]})
        p = program(0); p['kernels'][0]['name'] = 'rom_test'
        with self.assertRaises(Invalid): validate(p)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bad.json'
            path.write_text('{"version":1,"version":1}', encoding='ascii')
            with self.assertRaisesRegex(Invalid, 'duplicate'): load_json(path)

    def test_fallback_and_pool_validator(self):
        src = load_json(BENCH)
        with patch.object(propose, 'propose_kernel', side_effect=lambda k: {**driver._baseline(k), 'body': 123}):
            cert, _, st = driver.compile_source(src)
            self.assertEqual(len(st['fallback']), len(src['kernels']))
            checker.check_cert(src, cert)
            self.assertTrue(all(not k['steps'] for k in cert['kernels']))
        with patch.object(propose, 'propose_pool', return_value={'bytes':'','offsets':{}}):
            cert, _, st = driver.compile_source(src)
            self.assertTrue(st['fallback'] and cert['pool'] == propose.baseline_pool(blobs_of(src)))
        rng = random.Random(5)
        for _ in range(90):
            s = program('x'); data = bytes(rng.randrange(5) for _ in range(80)); blobs = []
            for i in range(12):
                off = rng.randrange(75); n = rng.randrange(30)
                blobs.append({'name':f'b{i}', 'align':1 << rng.randrange(6), 'hex':data[off:off+n].hex()})
            s['blobs'] = blobs
            c, _, st = driver.compile_source(s)
            self.assertFalse(st['fallback']); checker.check_cert(s, c)

    def test_pool_lower_bound_and_optimality_witness(self):
        def object_(name, data, align): return {'name':name,'hex':bytes(data).hex(),'align':align}
        # Includes vacuous and all-empty inputs, padding, odd sizes, repeated contents,
        # g-rounding and the case where this cheap lower bound fails to prove optimum.
        cases = [([], (0, 0, 1, 0, True)),
                 ([object_('z',[],256)], (0, 0, 1, 0, True)),
                 ([object_('a',[0,0,0,0],8)], (4, 1, 4, 4, True)),
                 ([object_('a',[0,1,2,3],4), object_('b',[3,4,5,6],4), object_('empty',[],1)], (8,7,4,4,True)),
                 ([object_('a',[1,2,3],4), object_('b',[3,4,5],4)], (5,5,1,3,False)),
                 ([object_('a',[1,2,3],1), object_('b',[3,4,5],1)], (5,5,1,3,True))]
        for objs, expected in cases:
            src = {**program('x'), 'blobs':objs}; cert, _, st = driver.compile_source(src)
            self.assertEqual(tuple(st[key] for key in ('pool_lower_bound_bytes','pool_distinct_octets',
                'pool_alignment_length_gcd_bytes','pool_longest_blob_bytes','pool_optimal')), expected)
            self.assertEqual(checker.check_pool(src, cert['pool'])['pool_optimal'], expected[-1])
        st = driver.compile_source(load_json(BENCH),'baseline')[2]
        self.assertEqual((st['pool_lower_bound_bytes'], st['pool_optimal']), (84,False))

    def test_native_generated_c(self):
        gcc = shutil.which(os.environ.get('RP2CC_HOST_CC', 'gcc')) or shutil.which('clang')
        if not gcc: self.skipTest('host C11 compiler not found')
        src = load_json(BENCH)
        names = ['rp_' + k['name'] for k in src['kernels']] + ['rp_rom_' + b['name'] for b in src['blobs']]
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp); seen = []
            for mode in ('baseline', 'optimized'):
                sub = d / mode; driver.write_artifacts(BENCH, sub, mode)
                driver.verify_artifacts(BENCH, sub / 'certificate.json')
                flags = ['-D' + n + '=b_' + n for n in names] if mode == 'baseline' else []
                obj = d / (mode + '.o')
                subprocess.run([gcc, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', *flags, '-c', str(sub/'kernels.c'), '-o', str(obj)], check=True)
                seen.append(str(obj))
            exe = d / ('differential.exe' if os.name == 'nt' else 'differential')
            subprocess.run([gcc, '-std=c11', '-O2','-Wall','-Wextra','-Werror', '-I', str(d/'optimized'), str(ROOT/'tests'/'test_compiler_host.c'), *seen, '-o', str(exe)], check=True)
            meta = json.loads(subprocess.check_output([str(exe)], text=True))
            self.assertEqual(meta['floor_word_cases'], 2*65536 + 12*12)
            self.assertEqual(meta['blob_value_cases'], 258*len(src['blobs']))
            print('native differential:', json.dumps(meta, sort_keys=True), flush=True)


if __name__ == '__main__': unittest.main()
