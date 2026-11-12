"""Synthetic encryption/resumption/inventory gates, never real manuscript or keys."""
import argparse
import base64
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import runtime as r


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        text='안녕하세요.'
        self.unit=dict(id='TB-001:p0000:u000',paragraph_id='TB-001:p0000',source=text,
            spoken=text,source_sha256=r.sha(text.encode()),mode='explain',start_utf16=0,end_utf16=len(text))
        self.plan=dict(lesson_id='TB-001',source=text,source_sha256=r.sha(text.encode()),units=[self.unit])
        self.settings=r.sha(b'fake-settings'); self.recipe=r.sha(b'fake-recipe'); self.key=bytes(range(32))
        self.receipt=dict(schema='bora-fp32-unit-receipt/v1',unit_id=self.unit['id'],
            source_sha256=self.unit['source_sha256'],spoken_sha256=r.sha(text.encode()),
            unit_key=r.identity(dict(unit=self.unit,render_settings_sha256=self.settings)),
            render_settings_sha256=self.settings,status='blocked_requires_review',production_eligible=False)

    def tearDown(self):
        self.temp.cleanup()

    def envelope(self):
        return r.seal_unit(self.key,self.plan,0,self.receipt,self.root,self.recipe,self.settings)

    def test_campaign_exact_303_and_selected_subject(self):
        campaign,recipe,request,ids=r.config()
        self.assertEqual(len(campaign['inputs']),303)
        expected=[f"{request['course']}-{i:03d}" for i in range(1,r.LIMITS[request['course']]+1)]
        self.assertEqual(ids,sorted(request.get('lesson_ids',expected)))
        self.assertIsInstance(request['retry_blocked'],bool)
        self.assertEqual(recipe['selection'],3)

    def test_all_course_crypto_namespaces(self):
        for course,count in r.LIMITS.items():
            for index in (1,count):
                ident=f'{course}-{index:03d}'
                m,c=r.crypto.seal(self.key,b'synthetic',ident,'checkpoint',r.KEY_ID)
                raw,_=r.crypto.open_range(self.key,m,c,expected_id=ident,expected_kind='checkpoint',expected_key_id=r.KEY_ID)
                self.assertEqual(raw,b'synthetic')
        with self.assertRaises(ValueError): r.crypto.namespace_for('MS-167')

    def test_checkpoint_encrypts_receipt(self):
        envelope=self.envelope()
        self.assertNotIn(self.unit['source'],r.enc(envelope).decode())
        row=r.open_unit(envelope,self.key,self.plan,self.recipe,self.settings,self.root/'open')
        self.assertEqual(row['receipt']['status'],'blocked_requires_review')
        self.assertFalse(row['path'].exists())

    def test_final_audio_roundtrip(self):
        raw=b'fake-test-bytes-not-production-audio'
        (self.root/'final.wav').write_bytes(raw)
        self.receipt.update(file='final.wav',sha256=r.sha(raw),status='passed')
        row=r.open_unit(self.envelope(),self.key,self.plan,self.recipe,self.settings,self.root/'open')
        self.assertEqual(row['path'].read_bytes(),raw)

    def test_wrong_settings_not_reused(self):
        row=r.open_unit(self.envelope(),self.key,self.plan,self.recipe,'a'*64,self.root/'open')
        self.assertIsNone(row)

    def test_changed_source_not_reused(self):
        plan=copy.deepcopy(self.plan); plan['source']='다른 원고'
        self.assertIsNone(r.open_unit(self.envelope(),self.key,plan,self.recipe,self.settings,self.root/'open'))

    def test_tampered_cipher_rejected(self):
        e=self.envelope(); c=bytearray(base64.b64decode(e['cipher_b64'])); c[-1]^=1
        e['cipher_b64']=base64.b64encode(c).decode()
        with self.assertRaises(ValueError): r.open_unit(e,self.key,self.plan,self.recipe,self.settings,self.root/'open')

    def test_cross_lesson_rejected(self):
        e=self.envelope(); e['lesson_id']='TB-002'
        with self.assertRaises(ValueError): r.open_unit(e,self.key,self.plan,self.recipe,self.settings,self.root/'open')

    def test_cross_unit_receipt_rejected_after_authentication(self):
        self.receipt['unit_id']='TB-001:p9999:u000'
        with self.assertRaises(ValueError):
            r.open_unit(self.envelope(),self.key,self.plan,self.recipe,self.settings,self.root/'open')

    def test_restore_only_authenticated_matching_envelope(self):
        incoming=self.root/'incoming'; incoming.mkdir()
        with zipfile.ZipFile(incoming/'TB-001-s000-r123-a1.zip','w') as z:
            z.writestr('report.json','{}')
            z.writestr('unit-00000.sealed.json',r.enc(self.envelope()))
        rows=r.restore(incoming,self.key,self.plan,self.recipe,self.settings,self.root/'restored')
        self.assertEqual(set(rows),{0})

    def test_untrusted_zip_path_rejected(self):
        incoming=self.root/'incoming'; incoming.mkdir()
        with zipfile.ZipFile(incoming/'TB-001-s000-r123-a1.zip','w') as z: z.writestr('../private','no')
        with self.assertRaises(ValueError):
            r.restore(incoming,self.key,self.plan,self.recipe,self.settings,self.root/'restored')

    def test_shard_coverage(self):
        plan=dict(lesson_id='TB-001',units=[dict(source='가'*100) for _ in range(91)])
        rows=r.shards(plan,4000)
        self.assertEqual([(x['start'],x['end']) for x in rows],[(0,40),(40,80),(80,91)])

    def test_incomplete_course_never_gets_index(self):
        with self.assertRaises(ValueError): r.make_index(argparse.Namespace(course='TB',directory=self.root))
        self.assertFalse((self.root/'index.json').exists())

    def test_unresolved_pronunciation_stops_before_render_matrix(self):
        plan=copy.deepcopy(self.plan); plan['paragraphs']=[]
        plan['units'][0]['review']=['review_remaining_middot_do_not_globally_split']
        args=argparse.Namespace(settings_sha='a'*64,output=self.root/'public-plan.json',
                                github_output=self.root/'github-output')
        with patch.object(r,'config',return_value=({},dict(shard_chars=4000),dict(course='TB'),['TB-001'])), \
             patch.object(r,'protected_keys',return_value=(self.key,b'')), \
             patch.object(r,'source_plan',return_value=(plan,{})):
            with self.assertRaisesRegex(ValueError,'source_review_required_before_render'):
                r.public_plan(args)
        self.assertFalse(args.github_output.exists())

    def test_unapproved_branch_fails_before_key_access(self):
        with patch.dict(os.environ,{'GITHUB_REPOSITORY':r.REPO,'GITHUB_REF':'refs/heads/other',
                                   'ADMIN_LAW_PREMIUM_SOURCE_KEY_B64':'SECRET-TEST-PLACEHOLDER'}):
            with self.assertRaises(ValueError): r.protected_keys()
            self.assertEqual(os.environ['ADMIN_LAW_PREMIUM_SOURCE_KEY_B64'],'SECRET-TEST-PLACEHOLDER')


if __name__=='__main__': unittest.main()
