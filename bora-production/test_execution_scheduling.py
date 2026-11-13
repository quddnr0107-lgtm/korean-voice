"""Synthetic scheduling/probe regression tests. No operating manuscripts or keys."""
import argparse
import ast
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

import runtime as r


class ExecutionSchedulingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.recipe={'shard_chars':4000}
        self.request={'course':'TB','retry_blocked':False,'lesson_ids':['TB-001']}
        self.settings='a'*64

    def tearDown(self):
        self.temp.cleanup()

    def plan(self, sizes, ident='TB-001'):
        units=[{'source':'가'*n,'spoken':'가'*n} for n in sizes]
        source=''.join(u['source'] for u in units)
        return {'lesson_id':ident,'source':source,'source_sha256':r.sha(source.encode()),
                'units':units,'paragraphs':[]}

    def public(self, plans, request=None):
        request=request or self.request
        args=argparse.Namespace(settings_sha=self.settings,output=self.root/'public.json',
                                github_output=self.root/'outputs')
        with patch.object(r,'config',return_value=({},self.recipe,request,list(plans))), \
             patch.object(r,'protected_keys',return_value=(b'synthetic',b'')), \
             patch.object(r,'source_plan',side_effect=lambda ident,*_: (plans[ident],{})), \
             contextlib.redirect_stdout(io.StringIO()):
            r.public_plan(args)
        result=json.loads(args.output.read_text())
        values=dict(line.split('=',1) for line in args.github_output.read_text().splitlines())
        return result,json.loads(values['matrix'])['include']

    def test_legacy_request_preserves_shards(self):
        plan=self.plan([100]*58)
        manifest,matrix=self.public({'TB-001':plan})
        self.assertEqual(manifest['lessons'][0]['shards'],r.shards(plan,4000))
        self.assertEqual(len(matrix),2)

    def test_fine_shards_preserve_plan_recipe_and_exact_coverage(self):
        plan=self.plan([100]*58); original=copy.deepcopy(plan)
        before=r.identity(plan); recipe_hash=r.identity(self.recipe)
        request=dict(self.request,execution_shard_chars=400)
        manifest,matrix=self.public({'TB-001':plan},request)
        self.assertEqual(len(matrix),15)
        ranges=manifest['lessons'][0]['shards']
        self.assertEqual([i for group in ranges for i in range(group['start'],group['end'])],list(range(58)))
        self.assertEqual(plan,original)
        self.assertEqual(manifest['lessons'][0]['plan_sha256'],before)
        self.assertEqual(manifest['recipe_sha256'],recipe_hash)
        self.assertEqual(manifest['render_settings_sha256'],self.settings)

    def test_schedule_changes_request_identity_not_audio_identity(self):
        old=dict(self.request); new=dict(old,execution_shard_chars=400)
        self.assertNotEqual(r.identity(old),r.identity(new))
        self.assertEqual(r.identity(self.recipe),r.identity(copy.deepcopy(self.recipe)))

    def test_invalid_schedule_rejected(self):
        for value in (True,False,None,'400',400.0,-1,0,199,4001,10**9):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError,'execution_shard_chars_bounds'):
                    r.execution_shard_chars(self.recipe,dict(self.request,execution_shard_chars=value))

    def test_boundary_schedule_values(self):
        for value in (200,400,800,2000,4000):
            self.assertEqual(r.execution_shard_chars(self.recipe,{'execution_shard_chars':value}),value)

    def test_randomized_coverage_no_unit_split_or_mutation(self):
        rng=random.Random(271828)
        for _ in range(200):
            sizes=[rng.randint(1,180) for _ in range(rng.randint(1,400))]
            plan=self.plan(sizes); fingerprint=r.identity(plan)
            target=rng.choice((200,400,800,2000,4000))
            groups=r.shards(plan,target)
            self.assertEqual([i for g in groups for i in range(g['start'],g['end'])],list(range(len(sizes))))
            self.assertEqual([g['shard'] for g in groups],list(range(len(groups))))
            self.assertTrue(all(g['end']>g['start'] for g in groups))
            self.assertTrue(all(sum(sizes[g['start']:g['end']])<=target for g in groups))
            self.assertEqual(r.identity(plan),fingerprint)

    def test_matrix_limit_cannot_be_bypassed(self):
        plan=self.plan([180]*257)
        with self.assertRaisesRegex(ValueError,'matrix_requires_lesson_batch'):
            self.public({'TB-001':plan},dict(self.request,execution_shard_chars=200))
        self.assertFalse((self.root/'outputs').exists())

    def test_long_spoken_work_prioritized_over_unit_count(self):
        first=self.plan([10]*4,'TB-001')
        second=self.plan([100],'TB-002')
        second['units'][0]['spoken']='나'*1000
        _,matrix=self.public({'TB-001':first,'TB-002':second})
        self.assertEqual(matrix[0]['lesson_id'],'TB-002')

    def test_source_review_still_blocks_before_matrix(self):
        plan=self.plan([100]*3); plan['units'][0]['review']=['synthetic-unresolved']
        with self.assertRaisesRegex(ValueError,'source_review_required_before_render'):
            self.public({'TB-001':plan},dict(self.request,execution_shard_chars=400))
        self.assertFalse((self.root/'outputs').exists())

    def test_reproduced_public_ranges_cannot_be_tampered(self):
        plan=self.plan([100]*58); request=dict(self.request,execution_shard_chars=400)
        manifest,_=self.public({'TB-001':plan},request)
        manifest['lessons'][0]['shards'][0]['end']+=1
        path=self.root/'tampered.json'; path.write_text(json.dumps(manifest))
        with patch.object(r,'source_plan',return_value=(plan,{})):
            with self.assertRaisesRegex(ValueError,'private_plan_reproduction'):
                r.matched_plan(argparse.Namespace(plan=path,lesson_id='TB-001'),{},self.recipe,request,b'synthetic')

    def test_reproduced_small_schedule_accepted(self):
        plan=self.plan([100]*58); request=dict(self.request,execution_shard_chars=400)
        self.public({'TB-001':plan},request)
        with patch.object(r,'source_plan',return_value=(plan,{})):
            _,again,_,row=r.matched_plan(argparse.Namespace(plan=self.root/'public.json',lesson_id='TB-001'),{},self.recipe,request,b'synthetic')
        self.assertEqual(again,plan)
        self.assertEqual(len(row['shards']),15)

    def test_missing_and_blocked_work_are_distinct(self):
        rows={0:{'receipt':{'status':'passed'}},1:{'receipt':{'status':'blocked_requires_review'}}}
        self.assertEqual(r.pending_units(rows,0,3,False),[2])
        self.assertEqual(r.pending_units(rows,0,3,True),[1,2])

    def probe(self, rows, retry=False, error=None):
        plan=self.plan([100]*4)
        args=argparse.Namespace(lesson_id='TB-001',shard=0,plan=self.root/'public.json',
            resume_dir=self.root/'incoming',output=self.root/'probe.json',github_output=self.root/'outputs')
        request=dict(self.request,retry_blocked=retry)
        public={'render_settings_sha256':self.settings}
        row={'shards':r.shards(plan,4000)}
        with patch.object(r,'config',return_value=({},self.recipe,request,['TB-001'])), \
             patch.object(r,'protected_keys',return_value=(b'synthetic',b'synthetic2')), \
             patch.object(r,'matched_plan',return_value=(public,plan,{},row)), \
             patch.object(r,'restore',return_value=rows,side_effect=error), \
             contextlib.redirect_stdout(io.StringIO()):
            r.resume_probe(args)
        return json.loads(args.output.read_text()),args.github_output.read_text()

    def test_all_passed_probe_needs_no_models(self):
        result,outputs=self.probe({i:{'receipt':{'status':'passed'}} for i in range(4)})
        self.assertFalse(result['needs_synthesis'])
        self.assertEqual((result['passed'],result['blocked'],result['pending']),(4,0,0))
        self.assertEqual(outputs,'needs_synthesis=false\n')

    def test_blocked_probe_does_not_blindly_regenerate(self):
        result,outputs=self.probe({i:{'receipt':{'status':'blocked_requires_review'}} for i in range(4)})
        self.assertFalse(result['needs_synthesis'])
        self.assertEqual((result['passed'],result['blocked'],result['pending']),(0,4,0))
        self.assertNotIn('approved',outputs)

    def test_missing_probe_requires_synthesis(self):
        result,outputs=self.probe({0:{'receipt':{'status':'passed'}}})
        self.assertTrue(result['needs_synthesis'])
        self.assertEqual(result['pending'],3)
        self.assertEqual(outputs,'needs_synthesis=true\n')

    def test_bad_checkpoint_fails_before_outputs(self):
        with self.assertRaisesRegex(ValueError,'synthetic-authentication-failure'):
            self.probe({},error=ValueError('synthetic-authentication-failure'))
        self.assertFalse((self.root/'outputs').exists())
        self.assertFalse((self.root/'probe.json').exists())

    def test_probe_never_exports_plaintext(self):
        result,_=self.probe({})
        self.assertNotIn('가',json.dumps(result,ensure_ascii=False))
        self.assertEqual(set(result),{'schema','lesson_id','shard','start','end','passed','blocked','pending',
            'needs_synthesis','request_sha256','plan_sha256','recipe_sha256','render_settings_sha256'})

    def test_probe_has_no_renderer_model_import(self):
        tree=ast.parse(Path(r.__file__).read_text())
        probe=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='resume_probe')
        self.assertFalse(any(isinstance(n,(ast.Import,ast.ImportFrom)) for n in ast.walk(probe)))
        self.assertNotIn('FP32Renderer',ast.unparse(probe))

    def test_checkpoint_identity_does_not_bind_request_or_shard(self):
        tree=ast.parse(Path(r.__file__).read_text())
        for name in ('seal_unit','open_unit'):
            node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
            text=ast.unparse(node)
            self.assertNotIn('request_sha256',text)
            self.assertNotIn('execution_shard_chars',text)
            self.assertIn('recipe_sha256',text)
            self.assertIn('render_settings_sha256',text)
            self.assertIn('plan_sha256',text)


if __name__=='__main__':
    unittest.main()
