"""Synthetic scheduling/probe regression tests. No operating manuscripts or keys."""
import argparse
import ast
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import random
import re
import tempfile
import textwrap
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


class MilitaryServiceCheckpointWorkflowTests(unittest.TestCase):
    """Exercise the actual keyless workflow guard and concurrency expression."""

    @classmethod
    def setUpClass(cls):
        cls.workflow = (Path(__file__).resolve().parent.parent /
                        '.github/workflows/bora-fp32-single-pass.yml').read_text()
        match = re.search(
            r"(?ms)^          PYTHONPATH=bora-production python - <<'PY'\n(.*?)^          PY\n",
            cls.workflow)
        if match is None:
            raise AssertionError('request validation script missing')
        cls.preflight_script = textwrap.dedent(match.group(1))

    def request(self, **changes):
        return dict(dict(
            schema='bora-fp32-request/v1',
            campaign_sha256='60e9763ab344e8a59264ec634a47ee60321667bc426108100f2973a04cfdca76',
            course='MS', execution_profile='bora-python-fp32-single-pass/v1',
            request_id='ms-checkpoint-workflow-test', retry_blocked=False,
            assembly_only=True, checkpoint_settings_sha256='a'*64,
            lesson_ids=['MS-044', 'MS-049', 'MS-052', 'MS-055', 'MS-057', 'MS-058'],
        ), **changes)

    def preflight(self, request, scope_flags=None):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'bora-production').mkdir()
            (root/'bora-production/single-pass-request.json').write_text(json.dumps(request))
            output = root/'outputs'
            flags = re.findall(r"os\.environ\['(BORA_[A-Z_]+)'\]", self.preflight_script)
            env = {name: 'false' for name in flags}
            env.update(BORA_MS_CHECKPOINT_ASSEMBLY='true', GITHUB_OUTPUT=str(output))
            if scope_flags is not None:
                env.update({name: 'false' for name in flags})
                env.update(scope_flags)
            with contextlib.chdir(root), patch.dict(os.environ, env, clear=True):
                exec(compile(self.preflight_script, '<workflow request guard>', 'exec'), {})
            return dict(line.split('=', 1) for line in output.read_text().splitlines())

    def concurrency_group(self, message, sha='0'*40):
        self.assertRegex(sha, r'^[a-f0-9]{40}\Z')
        group = self.workflow.split('concurrency:\n', 1)[1].split('\nenv:\n', 1)[0]
        self.assertIn('}}-${{ github.ref }}', group)
        self.assertIn('cancel-in-progress: false', group)
        expression = group.split('${{', 1)[1].split('}}', 1)[0]
        expression = re.sub(
            r"startsWith\(github\.event\.head_commit\.message,\s*'([^']+)'\)",
            lambda match: str(message.casefold().startswith(match.group(1).casefold())), expression)
        expression = re.sub(
            r"github\.event\.head_commit\.message\s*==\s*'([^']+)'",
            lambda match: str(message.casefold() == match.group(1).casefold()), expression)
        expression = re.sub(
            r"format\('exam80-export-recovery-\{0\}',\s*github\.sha\)",
            repr('exam80-export-recovery-' + sha), expression)
        expression = expression.replace('&&', 'and').replace('||', 'or').strip()
        tree = ast.parse('(' + expression + ')', mode='eval')
        allowed = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.Constant)
        self.assertTrue(all(isinstance(node, allowed) for node in ast.walk(tree)))
        return eval(compile(tree, '<workflow concurrency>', 'eval'), {'__builtins__': {}}, {})

    def test_correction_groups_require_full_messages_and_isolate_exports_by_commit(self):
        first = 'Render private Exam80 directive corrections 20261128'
        second = 'Render private Exam80 directive corrections DR038 DR047 20261128'
        recovery = 'Recover verified Exam80 correction exports for DR-038'
        self.assertEqual(self.concurrency_group(first), 'exam80-protected-dr-revision')
        self.assertEqual(self.concurrency_group(second), 'exam80-protected-dr-revision-038-047')
        for message in (first, second):
            self.assertEqual(self.concurrency_group(message + ' extra'), 'bora-fp32-single-pass-next')
        for sha in ('a'*40, 'b'*40):
            self.assertEqual(self.concurrency_group(recovery, sha), 'exam80-export-recovery-' + sha)

    def test_unrecognized_recovery_format_expression_is_rejected(self):
        altered, changes = re.subn(r"(format\('exam80-export-recovery-\{0\}',\s*)github\.sha\)",
                                   r"\1github.actor)", self.workflow)
        self.assertEqual(changes, 1)
        with patch.object(self, 'workflow', altered), self.assertRaises(AssertionError):
            self.concurrency_group('Recover verified Exam80 correction exports for DR-038')

    def test_ms_checkpoint_batches_share_one_group_without_waiting_for_directive_synthesis(self):
        first = self.concurrency_group('Bora MS checkpoint assembly: Recover six approved lessons')
        again = self.concurrency_group('Bora MS checkpoint assembly: Recover another approved batch')
        self.assertEqual(first, 'bora-fp32-single-pass-ms-assembly')
        self.assertEqual(first, again)
        self.assertNotEqual(first, self.concurrency_group('Bora DR completion: Finish the course'))
        self.assertNotEqual(first, self.concurrency_group('Bora MS production: Backfill remaining work'))
        self.assertEqual(self.concurrency_group('Bora DR completion: Finish the course'),
                         'bora-fp32-single-pass-next')
        self.assertEqual(self.concurrency_group('Bora MS production: Backfill remaining work'),
                         'bora-fp32-single-pass-ms-backfill')

    def test_ms_checkpoint_guard_accepts_explicit_bounded_recovery_and_keeps_the_pin(self):
        for ids in (['MS-001'], self.request()['lesson_ids'],
                    [f'MS-{index:03d}' for index in range(143, 167)]):
            with self.subTest(lesson_count=len(ids)):
                output = self.preflight(self.request(lesson_ids=ids))
                self.assertEqual(output, dict(assembly_only='true', course='MS',
                                              render_settings_sha256='a'*64))

    def test_ms_checkpoint_guard_rejects_synthesis_other_courses_and_unbounded_scope(self):
        missing_ids = self.request()
        missing_ids.pop('lesson_ids')
        synthesis = self.request()
        synthesis.pop('assembly_only')
        synthesis.pop('checkpoint_settings_sha256')
        invalid = [missing_ids, synthesis,
                   self.request(course='DR', lesson_ids=['DR-044']),
                   self.request(lesson_ids=[]), self.request(lesson_ids='MS-044'),
                   self.request(lesson_ids=[f'MS-{index:03d}' for index in range(1, 26)]),
                   self.request(lesson_ids=['MS-000']), self.request(lesson_ids=['MS-167']),
                   self.request(lesson_ids=['MS-044', 'MS-044']),
                   self.request(lesson_ids=[44]), self.request(render_workers=6),
                   self.request(retry_blocked=True), self.request(checkpoint_settings_sha256='bad')]
        for request in invalid:
            with self.subTest(request=request), self.assertRaises((SystemExit, ValueError)):
                self.preflight(request)

    def setup_request(self, **changes):
        request = self.request()
        request.pop('assembly_only')
        request.pop('checkpoint_settings_sha256')
        request.update(request_id='ms-setup-recovery-workflow-test', render_workers=1,
                       execution_shard_chars=2200,
                       lesson_ids=['MS-098', 'MS-104', 'MS-107', 'MS-111'],
                       selected_shards=[{'lesson_id': 'MS-104', 'shard': 3},
                                        {'lesson_id': 'MS-098', 'shard': 5},
                                        {'lesson_id': 'MS-111', 'shard': 0},
                                        {'lesson_id': 'MS-107', 'shard': 3}])
        request.update(changes)
        return request

    def test_ms_verified_fill_is_separate_and_keeps_all_production_gates(self):
        message = 'Bora MS production: Fill verified twenty-four source-cleared lessons'
        self.assertEqual(self.concurrency_group(message), 'bora-fp32-single-pass-ms-verified-fill')
        for prefix, suffix in (('Refill ', 'ms-refill'), ('Fill open ', 'ms-fill'),
                               ('Backfill ', 'ms-backfill'), ('Batch ', 'ms-pilot'),
                               ('Recover setup ', 'ms-setup-recovery')):
            self.assertEqual(self.concurrency_group('Bora MS production: ' + prefix + 'unchanged'),
                             'bora-fp32-single-pass-' + suffix)
        request = self.setup_request(render_workers=24,
                                     lesson_ids=[f'MS-{index:03d}' for index in range(117, 141)])
        request.pop('selected_shards')
        self.assertEqual(self.preflight(request, {'BORA_MS_PRODUCTION': 'true'}),
                         {'assembly_only': 'false'})
        values = {'needs.plan.result': 'success', 'needs.plan.outputs.overlap_tb': 'false',
                  'needs.plan.outputs.assembly_only': 'false', 'needs.plan.outputs.grouped_render': 'true',
                  'needs.capacity.result': 'success', 'needs.capacity.outputs.parallelism': '4',
                  'needs.render.result': 'skipped', 'needs.render_grouped.result': 'success',
                  'needs.assemble.result': 'success'}
        self.assertTrue(self.job_condition('capacity', message, values))
        self.assertTrue(self.job_condition('render_grouped', message, values))
        self.assertTrue(self.job_condition('assemble', message, values))
        self.assertFalse(self.job_condition('publish', message, values))
        values['needs.capacity.result'] = 'failure'
        values['needs.capacity.outputs.parallelism'] = ''
        self.assertFalse(self.job_condition('render_grouped', message, values))
        self.assertFalse(self.job_condition('assemble', message, values))

    def setup_preflight(self, request):
        return self.preflight(request, dict(BORA_MS_PRODUCTION='true', BORA_MS_SETUP_RECOVERY='true'))

    def test_setup_recovery_guard_accepts_only_bounded_explicit_work(self):
        for request in (self.setup_request(),
                        self.setup_request(lesson_ids=['MS-104'], selected_shards=[{'lesson_id': 'MS-104', 'shard': 3}]),
                        self.setup_request(lesson_ids=['MS-104'], selected_shards=[{'lesson_id': 'MS-104', 'shard': i} for i in range(6)])):
            with self.subTest(request=request):
                self.assertEqual(self.setup_preflight(request), {'assembly_only': 'false'})

    def test_setup_recovery_guard_rejects_expansion_ambiguity_and_type_confusion(self):
        invalid = [self.setup_request(render_workers=value) for value in (0, 2, True, 1.0, '1')]
        invalid += [self.setup_request(selected_shards=value) for value in
                    (None, {}, [], ['MS-104'], [{'lesson_id': 'MS-104', 'shard': i} for i in range(7)])]
        invalid += [self.setup_request(selected_shards=[{'lesson_id': 'MS-104', 'shard': value}])
                    for value in (True, '3', -1, 1000)]
        invalid += [self.setup_request(selected_shards=[row]) for row in
                    (None, {'lesson_id': 'MS-104'}, {'shard': 3},
                     {'lesson_id': 'MS-104', 'shard': 3, 'extra': 1},
                     {'lesson_id': 'DR-104', 'shard': 3}, {'lesson_id': 'MS-000', 'shard': 3})]
        duplicate = self.setup_request()
        duplicate['selected_shards'].append(copy.deepcopy(duplicate['selected_shards'][0]))
        missing = self.setup_request()
        missing.pop('selected_shards')
        invalid += [duplicate, missing,
                    self.setup_request(lesson_ids=['MS-098', 'MS-104', 'MS-107']),
                    self.setup_request(lesson_ids=['MS-098', 'MS-104', 'MS-107', 'MS-111', 'MS-112']),
                    self.setup_request(lesson_ids=['MS-098', 'MS-104', 'MS-107', 'MS-111', 'MS-111']),
                    self.setup_request(course='DR'), self.setup_request(retry_blocked=True),
                    self.setup_request(assembly_only=True, checkpoint_settings_sha256='a'*64)]
        for request in invalid:
            with self.subTest(request=request), self.assertRaises((SystemExit, ValueError)):
                self.setup_preflight(request)
        with self.assertRaises(SystemExit):
            self.preflight(self.setup_request(), {'BORA_MS_SETUP_RECOVERY': 'true'})

    def test_setup_recovery_concurrency_is_isolated_without_changing_existing_groups(self):
        message = 'Bora MS production: Recover setup 44046060122 group 13'
        self.assertEqual(self.concurrency_group(message), 'bora-fp32-single-pass-ms-setup-recovery')
        for prefix, suffix in (('Refill ', 'ms-refill'), ('Fill open ', 'ms-fill'),
                               ('Backfill ', 'ms-backfill'), ('Batch ', 'ms-pilot')):
            self.assertEqual(self.concurrency_group('Bora MS production: ' + prefix + 'unchanged'),
                             'bora-fp32-single-pass-' + suffix)

    def test_setup_recovery_smoke_requires_original_settings_without_pinning_other_runs(self):
        match = re.search(r"(?ms)^          python - <<'PY'\n(.*?)^          PY\n", self.workflow)
        self.assertIsNotNone(match)
        script = textwrap.dedent(match.group(1))
        original = '5597ef328c8f1381e634e45a00b11dd556a8423597d7dfc80771f3085649dd32'
        for recovery, course, fingerprint, accepted in ((True, 'MS', original, True), (True, 'MS', '0'*64, False),
                                                        (False, 'MS', '0'*64, True), (False, 'DR', '0'*64, True)):
            with self.subTest(recovery=recovery, course=course, fingerprint=fingerprint), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                smoke = dict(status='public_runtime_smoke_passed', source_key_used=False,
                             private_manuscript_read=False, course=course, asr_performed=False,
                             execution_profile='bora-python-fp32-single-pass/v1', max_attempts=1,
                             render_settings_sha256=fingerprint)
                (root/'bora-runtime-smoke.json').write_text(json.dumps(smoke))
                env = dict(RUNNER_TEMP=str(root), GITHUB_OUTPUT=str(root/'outputs'),
                           BORA_MS_SETUP_RECOVERY=str(recovery).lower())
                with patch.dict(os.environ, env, clear=True):
                    if accepted:
                        exec(compile(script, '<workflow smoke export>', 'exec'), {})
                        self.assertIn('render_settings_sha256='+fingerprint, (root/'outputs').read_text())
                    else:
                        with self.assertRaises(AssertionError):
                            exec(compile(script, '<workflow smoke export>', 'exec'), {})
                        self.assertFalse((root/'outputs').exists())

    def job_condition(self, job, message, values):
        body = re.search(r'(?ms)^  ' + job + r':\n(.*?)(?=^  [a-z_]+:|\Z)', self.workflow).group(1)
        expression = re.search(r'(?ms)^    if: >-\n(.*?)(?=^    [a-z_-]+:)', body).group(1)
        expression = expression.strip().removeprefix('${{').removesuffix('}}').strip()
        expression = re.sub(r"github\.event\.head_commit\.message\s*==\s*'([^']+)'",
                            lambda match: str(message == match.group(1)), expression)
        expression = re.sub(r"startsWith\(github\.event\.head_commit\.message, '([^']+)'\)",
                            lambda match: str(message.startswith(match.group(1))), expression)
        for key, value in sorted(values.items(), key=lambda item: -len(item[0])):
            expression = re.sub(r'\b' + re.escape(key) + r'\b', repr(value), expression)
        expression = expression.replace('always()', 'True').replace('cancelled()', 'False')
        expression = expression.replace('&&', 'and').replace('||', 'or')
        expression = re.sub(r'!(?!=)', 'not ', expression)
        tree = ast.parse('(' + expression + ')', mode='eval')
        allowed = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.Constant,
                   ast.Compare, ast.Eq, ast.NotEq, ast.UnaryOp, ast.Not)
        self.assertTrue(all(isinstance(node, allowed) for node in ast.walk(tree)))
        return eval(compile(tree, '<workflow job condition>', 'eval'), {'__builtins__': {}}, {})

    def test_ms_receipt_diagnostic_does_not_publish(self):
        values = {'needs.plan.result': 'success', 'needs.assemble.result': 'success',
                  'needs.plan.outputs.overlap_tb': 'false'}
        self.assertFalse(self.job_condition(
            'publish', 'Bora MS checkpoint assembly: Diagnose MS-018 MS-022 MS-034', values))
        self.assertTrue(self.job_condition(
            'publish', 'Bora MS checkpoint assembly: Recover verified checkpoints', values))

    def test_setup_recovery_retains_capacity_and_skips_full_lesson_assembly(self):
        message = 'Bora MS production: Recover setup 44046060122 group 13'
        values = {'needs.plan.result': 'success', 'needs.plan.outputs.overlap_tb': 'false',
                  'needs.plan.outputs.assembly_only': 'false', 'needs.plan.outputs.grouped_render': 'true',
                  'needs.capacity.result': 'success', 'needs.capacity.outputs.parallelism': '1',
                  'needs.render.result': 'skipped', 'needs.render_grouped.result': 'success',
                  'needs.assemble.result': 'success'}
        self.assertTrue(self.job_condition('capacity', message, values))
        self.assertTrue(self.job_condition('render_grouped', message, values))
        self.assertFalse(self.job_condition('assemble', message, values))
        self.assertFalse(self.job_condition('publish', message, values))
        values['needs.render_grouped.result'] = 'failure'
        original = 'Bora MS production: Refill original fifteen lessons'
        self.assertTrue(self.job_condition('assemble', original, values))
        self.assertFalse(self.job_condition('publish', original, values))


if __name__=='__main__':
    unittest.main()
