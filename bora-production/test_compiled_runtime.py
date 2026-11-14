"""Run profile boundaries against synthetic plans, keys and sealed receipts."""
from contextlib import nullcontext, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

import runtime as r


class CompiledRuntimeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)
        self.resume = self.work / 'incoming'
        self.resume.mkdir()
        self.key = b'm' * 32
        self.campaign, self.recipe, self.request, _ = r.config('compiled')
        text = '공개 시험 문장입니다.'
        self.unit = dict(id='TB-002:p0000:u000', source=text, spoken=text,
            source_sha256=r.sha(text.encode()))
        self.plan = dict(lesson_id='TB-002', source_sha256=self.unit['source_sha256'],
            units=[self.unit], paragraphs=[])
        self.settings = r.sha(b'synthetic-compiled-settings')
        self.old_settings = r.sha(b'synthetic-eager-settings')
        self.plan_file = self.work / 'plan.json'
        self.plan_file.write_bytes(r.enc(dict(schema='bora-fp32-public-plan/v1', course='TB',
            campaign_sha256=r.identity(self.campaign), recipe_sha256=r.identity(self.recipe),
            request_sha256=r.identity(self.request), render_settings_sha256=self.settings,
            lessons=[dict(lesson_id='TB-002', source_sha256=self.plan['source_sha256'],
                plan_sha256=r.identity(self.plan), unit_count=1, shards=r.shards(self.plan, 2000))])))
        self.args = SimpleNamespace(lesson_id='TB-002', plan=self.plan_file,
            resume_dir=self.resume, shard=0, output=self.work / 'probe.json', github_output=None)
        self.key_guard = self.enterContext(patch.object(r, 'protected_keys', return_value=(b's' * 32, self.key)))
        self.enterContext(patch.object(r, 'source_plan', return_value=(self.plan, {})))
        self.enterContext(patch.object(r, 'quiet', nullcontext))
        self.enterContext(patch.dict(r.os.environ, GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='1'))
        self.enterContext(patch.object(r.signal, 'signal'))

    def sealed_checkpoint(self, settings, claimed_settings=None):
        receipt = dict(schema='bora-fp32-unit-receipt/v1', unit_id=self.unit['id'],
            status='blocked_requires_review', production_eligible=False,
            source_sha256=self.unit['source_sha256'], spoken_sha256=self.unit['source_sha256'],
            unit_key=r.identity(dict(unit=self.unit, render_settings_sha256=settings)),
            render_settings_sha256=settings)
        envelope = r.seal_unit(self.key, self.plan, 0, receipt, self.work,
                               r.identity(self.recipe), settings)
        if claimed_settings is not None:
            envelope['render_settings_sha256'] = claimed_settings
        report = dict(schema='bora-fp32-shard-report/v1', lesson_id='TB-002', shard=0,
            start=0, end=1, passed=0, blocked=1, pending=0, timed_out=0,
            plan_sha256=r.identity(self.plan), recipe_sha256=r.identity(self.recipe),
            render_settings_sha256=claimed_settings or settings, run_id='1', run_attempt='1')
        with zipfile.ZipFile(self.resume / 'TB-002-s000-r1-a1.zip', 'w') as z:
            z.writestr('report.json', r.enc(report))
            z.writestr('unit-00000.sealed.json', r.enc(envelope))

    def test_eager_checkpoint_is_pending_under_compiled_settings(self):
        self.sealed_checkpoint(self.old_settings)
        with redirect_stdout(io.StringIO()):
            r.resume_probe(self.args, profile='compiled')
        result = json.loads(self.args.output.read_bytes())
        self.assertTrue(result['needs_synthesis'])
        self.assertEqual((result['blocked'], result['pending']), (0, 1))
        self.key_guard.assert_called_once_with(media=True, profile='compiled')

    def test_relabelled_eager_checkpoint_is_rejected_before_renderer_load(self):
        self.sealed_checkpoint(self.old_settings, claimed_settings=self.settings)
        self.args.output = self.work / 'cipher'
        with patch.object(r, 'make_renderer') as renderer, \
             self.assertRaisesRegex(ValueError, 'authenticated_checkpoint_identity'):
            r.render(self.args, profile='compiled')
        renderer.assert_not_called()

    def test_restored_blocked_checkpoint_skips_renderer_and_preserves_report(self):
        self.sealed_checkpoint(self.settings)
        self.args.output = self.work / 'cipher'
        with patch.object(r, 'make_renderer') as renderer, redirect_stdout(io.StringIO()):
            r.render(self.args, profile='compiled')
        renderer.assert_not_called()
        with zipfile.ZipFile(next(self.args.output.glob('*.zip'))) as z:
            self.assertEqual(z.namelist(), ['report.json'])
            report = json.loads(z.read('report.json'))
        self.assertEqual((report['passed'], report['blocked'], report['pending']), (0, 1, 0))

    def test_renderer_fingerprint_failure_closes_compiled_adapter(self):
        self.args.output = self.work / 'cipher'
        adapter = Mock(settings_sha256=self.old_settings)
        factory = Mock(return_value=adapter)
        with patch.dict(sys.modules, {'compiled_fp32_renderer': SimpleNamespace(CompiledFP32Renderer=factory)}), \
             patch.dict(r.os.environ, BORA_MODEL='public-model', BORA_ASR='public-asr'), \
             redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, 'preflight_renderer_fingerprint'):
            r.render(self.args, profile='compiled')
        factory.assert_called_once_with('public-model', 'public-asr', max_attempts=self.recipe['max_attempts'])
        adapter.render_unit.assert_not_called()
        adapter.close.assert_called_once_with()
        self.assertTrue(list(self.args.output.glob('*.zip')))

    def test_compiled_preflight_closes_adapter_when_public_qa_blocks(self):
        adapter = Mock(settings_sha256=self.settings)
        adapter.render_unit.return_value = {'status': 'blocked_requires_review'}
        assemble = Mock()
        with patch.dict(sys.modules, {'planning': SimpleNamespace(build_plan=lambda *a: self.plan),
                                      'assemble': SimpleNamespace(assemble_lesson=assemble)}), \
             patch.object(r, 'make_renderer', return_value=adapter), \
             self.assertRaisesRegex(ValueError, 'public_smoke_not_passed'):
            r.preflight(self.args, profile='compiled')
        adapter.close.assert_called_once_with()
        assemble.assert_not_called()
        self.key_guard.assert_not_called()
        self.assertFalse(self.args.output.exists())

    def test_tb001_and_partial_index_fail_before_protected_work(self):
        self.args.lesson_id = 'TB-001'
        for command in (r.resume_probe, r.render, r.assemble):
            with self.subTest(command=command.__name__), self.assertRaisesRegex(ValueError, 'compiled_lesson_scope'):
                command(self.args, profile='compiled')
        self.key_guard.assert_not_called()
        with patch.object(sys, 'argv', ['compiled_runtime.py', 'index', '--directory', str(self.work), '--course', 'TB']), \
             patch.object(r.os, 'umask'), patch.object(r, 'make_index') as index, \
             self.assertRaisesRegex(ValueError, 'compiled_course_index_forbidden'):
            r.main(profile='compiled')
        index.assert_not_called()


if __name__ == '__main__':
    unittest.main()
