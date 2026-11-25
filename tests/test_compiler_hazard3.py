import json
import copy
import tempfile
import unittest
from pathlib import Path

from rp2cc.driver import write_artifacts
from tools import tune_hazard3 as tune
from tools.check_hazard3_code import check_words, selected_names


class Hazard3Tests(unittest.TestCase):
    def setUp(self):
        self.box=tempfile.TemporaryDirectory(); self.addCleanup(self.box.cleanup); self.d=Path(self.box.name)
        self.p=self.d/'ir.json'; self.base=self.d/'gen'; self.out=self.d/'tuned'
        self.ir={'version':1,'kernels':[{'name':'arbitrary_name','args':['q'],'body':['floor2','q']},
                 {'name':'aff','args':['q'],'body':['affine_iter','q',257,1664525,1013904223]},
                 {'name':'nested','args':['q'],'body':['add',['floor2','q'],1]}]}
        self.p.write_text(json.dumps(self.ir),encoding='utf-8'); write_artifacts(self.p,self.base,'optimized')

    def test_hazard3_selection_and_unchanged_rest(self):
        rec=tune.tune(self.p,self.base,self.out); self.assertEqual([x['kernel'] for x in rec['selected']],['arbitrary_name'])
        self.assertTrue(tune.verify(self.p,self.base,self.out)==rec)
        self.assertFalse((self.out/'certificate.json').exists()); self.assertTrue((self.out/'portable'/'certificate.json').exists())
        old=(self.base/'kernels.c').read_text(encoding='ascii'); new=(self.out/'kernels.c').read_text(encoding='ascii')
        self.assertEqual(old[old.index('uint32_t rp_aff'):],new[new.index('uint32_t rp_aff'):]); self.assertEqual(new.count('__asm__'),1)
        self.assertEqual((self.base/'kernels.h').read_bytes(),(self.out/'kernels.h').read_bytes())

    def test_hazard3_rejects_malformed_inputs_and_targets(self):
        write_artifacts(self.p,self.base,'baseline')
        with self.assertRaises(ValueError): tune.tune(self.p,self.base,self.out)
        write_artifacts(self.p,self.base,'optimized')
        with (self.base/'kernels.c').open('a') as f: f.write('/* changed */')
        with self.assertRaises(ValueError): tune.tune(self.p,self.base,self.out)
        self.ir['kernels']=self.ir['kernels'][1:]; self.p.write_text(json.dumps(self.ir),encoding='utf-8'); write_artifacts(self.p,self.base,'optimized')
        with self.assertRaises(ValueError): tune.tune(self.p,self.base,self.out)

    def test_hazard3_receipt_replays_bytes_not_just_hashes(self):
        tune.tune(self.p,self.base,self.out)
        p=self.out/'kernels.c'; p.write_bytes(p.read_bytes().replace(b', 20\\n',b', 19\\n'))
        rec_path=self.out/'hazard3.json'; r=json.loads(rec_path.read_text()); r['target_files']['kernels.c']=tune.sha(p.read_bytes())
        rec_path.write_bytes(tune._receipt_bytes(r))
        with self.assertRaises(ValueError): tune.verify(self.p,self.base,self.out)

    def test_hazard3_machine_word_decoder(self):
        raw=bytes.fromhex('931755609187518533c5a7088280')
        self.assertEqual(check_words(raw)['temp_gpr'],15)
        for i in range(len(raw)):
            bad=bytearray(raw); bad[i]^=1
            with self.assertRaises(ValueError,msg=i): check_words(bad)
        for bad in (raw+b'\x01\x00',raw[:-2],raw[:-1]):
            with self.assertRaises(ValueError): check_words(bad)

    def test_binary_receipt_shape(self):
        rec=tune.tune(self.p,self.base,self.out)
        self.assertEqual(selected_names(rec),['arbitrary_name'])
        for mutate in (lambda r:r['selected'].append(r['selected'][0]),
                       lambda r:r['selected'][0].update(argument='bad semicolon;'),
                       lambda r:r.update(value_sequence=[]),
                       lambda r:r['target_files'].update({'kernels.c':'123'})):
            bad=copy.deepcopy(rec); mutate(bad)
            with self.assertRaises(ValueError): selected_names(bad)
