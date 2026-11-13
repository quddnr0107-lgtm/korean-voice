"""Synthetic encrypted-range regression tests; no real manuscripts or keys."""
import ast
import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from cryptography.exceptions import InvalidTag
import runtime as r
from transport import TransportError


class RestoreRangeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.incoming=self.root/'incoming';self.incoming.mkdir()
        self.key=bytes(range(32));self.settings=r.sha(b'synthetic-settings')
        self.recipe=r.sha(b'synthetic-recipe')
        units=[]
        for i in range(4):
            text=f'공개 시험 문장 {i}.'
            units.append(dict(id=f'TB-001:p0000:u{i:03d}',paragraph_id='TB-001:p0000',
                source=text,spoken=text,source_sha256=r.sha(text.encode()),mode='explain',
                start_utf16=i*20,end_utf16=i*20+len(text)))
        self.plan=dict(lesson_id='TB-001',source=''.join(u['source'] for u in units),units=units)
        self.envelopes={}
        for i,unit in enumerate(units):
            folder=self.root/f'input-{i}';folder.mkdir()
            audio=f'SYNTHETIC_PCM_{i}'.encode();(folder/'final.wav').write_bytes(audio)
            receipt=dict(schema='bora-fp32-unit-receipt/v1',unit_id=unit['id'],
                source_sha256=unit['source_sha256'],spoken_sha256=r.sha(unit['spoken'].encode()),
                unit_key=r.identity(dict(unit=unit,render_settings_sha256=self.settings)),
                render_settings_sha256=self.settings,status='passed',production_eligible=True,
                file='final.wav',sha256=r.sha(audio))
            self.envelopes[i]=r.seal_unit(self.key,self.plan,i,receipt,folder,self.recipe,self.settings)
        self.report=dict(schema='bora-fp32-shard-report/v1',lesson_id='TB-001',shard=0,
            start=0,end=4,passed=4,blocked=0,pending=0,timed_out=0,
            plan_sha256=r.identity(self.plan),recipe_sha256=self.recipe,
            render_settings_sha256=self.settings,run_id='123',run_attempt='1')
        self.bundle=self.incoming/'TB-001-s000-r123-a1.zip'
        self.write_bundle()

    def tearDown(self):
        self.temp.cleanup()

    def write_bundle(self,report=None,envelopes=None):
        with zipfile.ZipFile(self.bundle,'w') as z:
            z.writestr('report.json',r.enc(self.report if report is None else report))
            for i,e in (self.envelopes if envelopes is None else envelopes).items():
                z.writestr(f'unit-{i:05d}.sealed.json',r.enc(e))

    def restore(self,name,**kwargs):
        return r.restore(self.incoming,self.key,self.plan,self.recipe,self.settings,
                         self.root/name,**kwargs)

    def test_range_matches_full_authenticated_receipts_and_audio(self):
        full=self.restore('full')
        with patch.object(r,'open_unit',wraps=r.open_unit) as opened:
            part=self.restore('part',unit_range=(1,3))
        self.assertEqual(set(full),{0,1,2,3})
        self.assertEqual(set(part),{1,2})
        self.assertEqual(opened.call_count,2)
        for i in part:
            self.assertEqual(part[i]['receipt'],full[i]['receipt'])
            self.assertEqual(part[i]['path'].read_bytes(),full[i]['path'].read_bytes())
        self.assertFalse((self.root/'part/u00000').exists())
        self.assertFalse((self.root/'part/u00003').exists())

    def test_all_explicit_ranges_match_full_selection(self):
        full=self.restore('full')
        for start in range(4):
            for end in range(start+1,5):
                part=self.restore(f'part-{start}-{end}',unit_range=(start,end))
                self.assertEqual(set(part),set(range(start,end)))
                self.assertEqual({i:p['receipt'] for i,p in part.items()},
                                 {i:full[i]['receipt'] for i in range(start,end)})

    def test_invalid_bounds_fail_before_opening_any_bundle(self):
        for bounds in ((-1,1),(0,0),(2,1),(0,5),(False,2),(0,True),
                       (0,2.0),[0,2],(0,),(),('0',2)):
            with self.subTest(bounds=bounds),patch.object(r,'open_unit') as opened:
                with self.assertRaisesRegex(ValueError,'resume_unit_range'):
                    self.restore('invalid',unit_range=bounds)
                opened.assert_not_called()

    def test_tampered_in_range_cipher_still_requires_authenticated_decryption(self):
        envelopes=copy.deepcopy(self.envelopes)
        blob=bytearray(base64.b64decode(envelopes[1]['cipher_b64']));blob[-1]^=1
        # Even an attacker who rewrites public hashes cannot forge the AES tag.
        envelopes[1]['cipher_b64']=base64.b64encode(blob).decode()
        envelopes[1]['manifest']['cipher_sha256']=[r.sha(bytes(blob))]
        self.write_bundle(envelopes=envelopes)
        with self.assertRaises(InvalidTag):self.restore('bad',unit_range=(1,2))

    def test_outside_range_public_cipher_corruption_is_not_skipped(self):
        envelopes=copy.deepcopy(self.envelopes)
        blob=bytearray(base64.b64decode(envelopes[3]['cipher_b64']));blob[-1]^=1
        envelopes[3]['cipher_b64']=base64.b64encode(blob).decode()
        self.write_bundle(envelopes=envelopes)
        with patch.object(r,'open_unit') as opened:
            with self.assertRaises(TransportError):self.restore('bad',unit_range=(0,1))
            opened.assert_not_called()

    def test_entire_report_and_envelope_pins_validated_before_range_skip(self):
        mutations=((lambda p,e:p.update(passed=3),TransportError),
                   (lambda p,e:e[3].update(unit_index=2),TransportError),
                   (lambda p,e:e[3].update(recipe_sha256='a'*64),TransportError),
                   (lambda p,e:p.update(end=99999,passed=99999),ValueError))
        for mutate,error in mutations:
            report=copy.deepcopy(self.report);envelopes=copy.deepcopy(self.envelopes)
            mutate(report,envelopes);self.write_bundle(report,envelopes)
            with self.subTest(mutate=mutate),patch.object(r,'open_unit') as opened:
                with self.assertRaises(error):self.restore('bad',unit_range=(0,1))
                opened.assert_not_called()

    def test_incompatible_fingerprint_not_reused(self):
        altered=copy.deepcopy(self.plan);altered['source']='새 원고'
        rows=r.restore(self.incoming,self.key,altered,self.recipe,self.settings,
                       self.root/'different',unit_range=(0,1))
        self.assertEqual(rows,{})

    def test_probe_render_are_scoped_and_assembly_remains_full(self):
        tree=ast.parse(Path(r.__file__).read_text())
        for name in ('resume_probe','render','assemble'):
            node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
            calls=[n for n in ast.walk(node) if isinstance(n,ast.Call)
                   and isinstance(n.func,ast.Name) and n.func.id=='restore']
            self.assertEqual(len(calls),1)
            kwargs={k.arg:ast.unparse(k.value) for k in calls[0].keywords}
            if name=='assemble':self.assertNotIn('unit_range',kwargs)
            else:self.assertEqual(kwargs.get('unit_range'),'(begin, end)')


if __name__=='__main__':unittest.main()
