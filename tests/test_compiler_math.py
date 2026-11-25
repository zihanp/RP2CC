import random
import unittest
from pathlib import Path

from rp2cc.driver import compile_source
from rp2cc.model import Invalid, MASK, evaluate
from tools.math_frontend import translate

ROOT = Path(__file__).resolve().parents[1]


def fun(code, args='x: u32'):
    return 'def f(' + args + ') -> u32:\n' + '\n'.join('    '+s for s in code.splitlines()) + '\n'


class MathFrontTests(unittest.TestCase):
    def test_math_example_and_regular_loop(self):
        src, audit = translate((ROOT/'examples'/'jump.rpm').read_text(encoding='utf-8'))
        self.assertEqual(src['kernels'][0]['body'], ['affine_iter','x',257,1664525,1013904223])
        self.assertEqual(src['kernels'][2]['body'], ['affine_iter','x',3,7,11])
        self.assertEqual([(s['state'],s['iterations']) for s in audit['recurrences']], [('x',257),('y',3)])
        cert,_,summary = compile_source(src)
        self.assertFalse(summary['fallback'])
        self.assertEqual(cert['kernels'][0]['body'], ['add',['mul','x',0x46a65a0d],0xe89f545f])

    def test_math_random_affine_derivation(self):
        rng = random.Random(0x2354)
        for _ in range(90):
            p,q,r,t,n = (rng.getrandbits(32),rng.getrandbits(32),rng.getrandbits(32),rng.getrandbits(32),rng.randrange(17))
            src,audit = translate(fun(f'k = {p}\nfor _ in range({n}):\n    x = k * (x - {q}) + {r} * x + {t}\nreturn x'))
            cert,_,st = compile_source(src)
            self.assertFalse(st['fallback'])
            self.assertEqual((audit['recurrences'][0]['multiplier'],audit['recurrences'][0]['increment']), ((p+r)&MASK,(t-p*q)&MASK))
            for init in (0,MASK,rng.getrandbits(32)):
                v = init
                for _step in range(n): v = (p*(v-q) + r*v + t) & MASK
                self.assertEqual(evaluate(cert['kernels'][0]['body'],{'x':init}),v)

    def test_math_assignments_and_word_notation(self):
        src,_ = translate('K=0x0f\n'+fun('saved=x+1\nx=7\ny = (~saved & K) ^ (saved >> (K - 12))\nreturn y | ((-saved) << 1)'))
        cert,_,st = compile_source(src)
        self.assertFalse(st['fallback'])
        for x in (0,1,MASK,0x80000001):
            v=(x+1)&MASK
            want=((~v&15)^(v>>3)) | ((-v<<1)&MASK)
            self.assertEqual(evaluate(cert['kernels'][0]['body'],{'x':x}),want)
        s,_=translate(fun('for _ in range(0):\n    x = -x + 9\nreturn x'))
        self.assertEqual(compile_source(s)[0]['kernels'][0]['body'],'x')

    def test_math_rejects_nonlinearity_dynamic_terms_and_effects(self):
        for expression in ('x*x','x+y','y*x','x<<1','x//2','read_register()','x.attr','lane_floor16(x)'):
            with self.assertRaises(Invalid, msg=expression):
                translate(fun(f'for _ in range(3):\n    x = {expression}\nreturn x','x: u32, y: u32'))
        for code in ('for i in range(2):\n    x = x+1\nreturn x',
                     'for _ in range(x):\n    x = x+1\nreturn x',
                     'for _ in range(65536):\n    x = x+1\nreturn x',
                     'for _ in range(2):\n    x = x+1\n    y = x\nreturn x',
                     'y=1\nfor _ in range(3):\n    y = (y & 7) + 1\nreturn y',
                     'return x >> 32','return -1.0','return x+True','return x\nx=x+1',
                     'reg[0]=x\nreturn x','x = unknown_call(1)\nreturn x','while x:\n    x=x-1\nreturn x'):
            with self.assertRaises(Invalid, msg=code): translate(fun(code))
        for source in ('import os\n'+fun('return x'), 'X=1\n'+fun('X=2\nreturn x'),
                       'X=1\n'+fun('return X','X: u32'), 'def f(x) -> u32:\n    return x'):
            with self.assertRaises(Invalid): translate(source)
