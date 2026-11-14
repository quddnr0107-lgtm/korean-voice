import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import execution_profiles as profiles
import render_batches as batches
import runtime


class RenderBatchTests(unittest.TestCase):
    def request(self, **changes):
        return dict(dict(schema='bora-fp32-request/v1', campaign_sha256=profiles.CAMPAIGN_SHA256,
                         execution_profile=profiles.SINGLE_PASS_PROFILE_ID, retry_blocked=False,
                         request_id='worker-test', course='DR', render_workers=40), **changes)

    def test_bounded_deterministic_partition_of_protected_shards(self):
        shards = [{'lesson_id': f'DR-{i // 6 + 1:03}', 'shard': i % 6, 'start': i, 'end': i + 1}
                  for i in range(236)]
        pairs = [(row['lesson_id'], row['shard']) for row in shards]
        costs = {pair: 1000 - i * 3 for i, pair in enumerate(pairs)}
        work = batches.group_render_work(shards, costs, 40)
        self.assertEqual(work, batches.group_render_work(shards[::-1], costs, 40))
        self.assertEqual(len(work), 40)
        self.assertTrue(all(1 <= len(row['items']) <= 6 for row in work))
        self.assertCountEqual(pairs, [(s['lesson_id'], s['shard']) for row in work for s in row['items']])
        self.assertEqual(set(work[0]['items'][0]), {'lesson_id', 'shard'})
        with self.assertRaisesRegex(ValueError, 'smaller_lesson_batch'):
            batches.group_render_work(shards, costs, 39)

    def test_opt_in_caps_and_disjoint_execution_modes(self):
        profiles.validate_single_pass_request(self.request())
        for changes in ({'render_workers': True}, {'render_workers': 0}, {'render_workers': 41},
                        {'assembly_only': True, 'checkpoint_settings_sha256': 'f'*64},
                        {'course':'RL', 'overlap_tb':True, 'lesson_ids':['RL-001']}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'render_workers_scope'):
                profiles.validate_single_pass_request(self.request(**changes))

    def test_planner_preserves_coverage_and_uses_private_costs_only_for_scheduling(self):
        plans = {id_: ({'lesson_id': id_, 'source_sha256': 'a'*64, 'paragraphs': [{}],
                        'units': [{'source':'private text'*10}] * count}, {})
                 for id_, count in (('DR-001', 9), ('DR-002', 5))}
        request = self.request(render_workers=3, execution_shard_chars=250)
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(runtime, 'config', return_value=({}, {'shard_chars':4000}, request, list(plans))), \
             patch.object(runtime, 'protected_keys', return_value=(b'source', b'')), \
             patch.object(runtime, 'course_plan', side_effect=lambda id_, *_: plans[id_]), \
             contextlib.redirect_stdout(io.StringIO()):
            args = argparse.Namespace(settings_sha='b'*64, output=Path(temp)/'manifest.json',
                                      github_output=Path(temp)/'outputs')
            runtime.public_plan(args, profile='single_pass')
            outputs = dict(line.split('=', 1) for line in args.github_output.read_text().splitlines())
            manifest = json.loads(args.output.read_text())
            self.assertEqual(outputs['grouped_render'], 'true')
            self.assertEqual(outputs['parallelism'], '3')
            self.assertEqual(len(json.loads(outputs['matrix'])['include']), 3)
            self.assertNotIn('private text', args.output.read_text() + args.github_output.read_text())
            for row in manifest['lessons']:
                plan = plans[row['lesson_id']][0]
                self.assertEqual(row['plan_sha256'], runtime.identity(plan))
                self.assertEqual(row['shards'], runtime.shards(plan, 250))
            self.assertEqual(manifest['render_settings_sha256'], 'b'*64)

    def test_input_scope_and_soft_deadline_are_checked_at_each_slot(self):
        assignment = [{'lesson_id': 'DR-001', 'shard': 0}, {'lesson_id': 'DR-001', 'shard': 1}]
        self.assertFalse(batches.assignment(assignment, 1, 1, 'DR', 2000, 500)['deferred'])
        self.assertTrue(batches.assignment(assignment, 1, 2, 'DR', 2000, 501)['deferred'])
        self.assertIsNone(batches.assignment(assignment, 1, 3, 'DR', 2000, 501))
        for rows in (assignment + assignment, [{'lesson_id': 'RL-001', 'shard': 2}],
                     [{'lesson_id': 'DR-001/../../etc', 'shard': 0}]):
            with self.assertRaisesRegex(ValueError, 'render_worker_item'):
                batches.assignment(rows, 1, 1, 'DR', 2000, 500)

    def test_expired_shared_deadline_seals_report_without_loading_model(self):
        plan = {'lesson_id':'DR-001', 'units':[{'source':'synthetic'}]}
        row = {'shards':[{'start':0, 'end':1}]}
        recipe = {'soft_minutes':270, 'unit_timeout_seconds':1200}
        request = self.request()
        with tempfile.TemporaryDirectory() as temp, \
             patch.dict(os.environ, {'GITHUB_RUN_ID':'42', 'GITHUB_RUN_ATTEMPT':'1', 'RUNNER_TEMP':temp}), \
             patch.object(runtime, 'config', return_value=({}, recipe, request, ['DR-001'])), \
             patch.object(runtime, 'protected_keys', return_value=(b'source', b'media')), \
             patch.object(runtime, 'matched_plan', return_value=({'render_settings_sha256':'b'*64}, plan, {}, row)), \
             patch.object(runtime, 'restore', return_value={}), \
             patch.object(runtime, 'make_renderer') as renderer, \
             patch.object(runtime.signal, 'signal'), \
             patch.object(runtime.time, 'monotonic', return_value=1000), \
             contextlib.redirect_stdout(io.StringIO()):
            args = argparse.Namespace(lesson_id='DR-001', shard=0, resume_dir=temp,
                                      output=Path(temp)/'out', worker_deadline=999)
            with self.assertRaisesRegex(ValueError, 'render_worker_shard_incomplete'):
                runtime.render(args, profile='single_pass')
            renderer.assert_not_called()
            with zipfile.ZipFile(args.output/'DR-001-s000-r42-a1.zip') as checkpoint:
                report = json.loads(checkpoint.read('report.json'))
                self.assertEqual((report['schema'], report['passed'], report['pending']),
                                 ('bora-fp32-shard-report/v1', 0, 1))

    def test_workflow_keeps_same_protection_and_separates_authorization_inputs(self):
        workflow = (runtime.ROOT/'.github/workflows/bora-fp32-single-pass.yml').read_text()
        grouped, tail = workflow.split('\n  render_grouped:\n')[1].split('\n  assemble:\n')
        action = (runtime.ROOT/'.github/actions/bora-render-slot/action.yml').read_text()
        self.assertIn('environment: admin-law-premium-production', grouped)
        self.assertEqual(grouped.count('uses: ./.github/actions/bora-render-slot'), 6)
        self.assertIn("needs.render_grouped.result == 'failure'", tail)
        steps = action.split('\n    - ')[1:]
        for step in steps:
            if 'inputs.github-token' in step:
                self.assertNotIn('inputs.source-key', step)
                self.assertNotIn('inputs.media-key', step)
            if 'inputs.source-key' in step:
                self.assertIn('unshare --net --setgid', step)
                self.assertNotIn('GITHUB_TOKEN', step)
        self.assertIn('transport.py upload', action)


if __name__ == '__main__':
    unittest.main()
