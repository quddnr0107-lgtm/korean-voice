"""Exercise single-pass scope and routing without protected inputs."""
import argparse
import contextlib
import io
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import execution_profiles as profiles
import runtime


class SinglePassRuntimeTests(unittest.TestCase):
    def request(self, **overrides):
        return dict(dict(schema='bora-fp32-request/v1',
            campaign_sha256=profiles.CAMPAIGN_SHA256,
            execution_profile=profiles.SINGLE_PASS_PROFILE_ID,
            retry_blocked=False, request_id='single-pass-test', course='TB'), **overrides)

    def test_only_the_scoped_single_attempt_profile_is_accepted(self):
        for course in runtime.LIMITS:
            profiles.validate_request(self.request(course=course), 'single_pass')
        for overrides in (dict(course='ALL'), dict(retry_blocked=0), dict(retry_blocked=True),
                          dict(max_attempts=2), dict(execution_profile=profiles.PROFILE_ID)):
            with self.subTest(overrides=overrides), self.assertRaisesRegex(ValueError, 'request_scope'):
                profiles.validate_request(self.request(**overrides), 'single_pass')
        self.assertEqual(profiles.workflow_filename('single_pass'), 'bora-fp32-single-pass.yml')
        self.assertEqual(profiles.request_filename('single_pass'), 'single-pass-request.json')

    def test_reject_other_workflow_before_namespace_or_keys(self):
        wrong = dict(GITHUB_REPOSITORY=runtime.REPO, GITHUB_EVENT_NAME='push',
            GITHUB_REF='refs/heads/'+runtime.BRANCH,
            GITHUB_WORKFLOW_REF=runtime.REPO+'/.github/workflows/bora-fp32-production.yml@refs/heads/'+runtime.BRANCH)
        with patch.dict(os.environ, wrong, clear=True), patch.object(runtime, 'validate_snapshot') as guard:
            with self.assertRaisesRegex(ValueError, 'workflow_context'):
                runtime.protected_keys(profile='single_pass')
            guard.assert_not_called()

    def test_enforce_plan_selection_before_fetching_keys(self):
        request = self.request(selected_shards=[{'lesson_id': 'TB-001', 'shard': 0}])
        args = argparse.Namespace(lesson_id='TB-001', shard=1)
        with patch.object(runtime, 'config', return_value=({}, {}, request, ['TB-001'])), \
             patch.object(runtime, 'protected_keys') as keys:
            for operation in (runtime.resume_probe, runtime.render):
                with self.assertRaisesRegex(ValueError, 'single_pass_selected_shard'):
                    operation(args, profile='single_pass')
            keys.assert_not_called()

    def test_factory_needs_only_model_not_asr(self):
        renderer = Mock()
        module = types.SimpleNamespace(SinglePassRenderer=renderer)
        with patch.dict('sys.modules', {'single_pass_renderer': module}), \
             patch.dict(os.environ, {'BORA_MODEL': 'verified-model'}, clear=True):
            actual = runtime.make_renderer({'max_new_tokens': 1100, 'max_attempts': 2}, 'single_pass')
        renderer.assert_called_once_with('verified-model', max_new_tokens=1100, max_attempts=1)
        self.assertIs(actual, renderer.return_value)

    def test_plan_emits_coverage_without_private_source(self):
        request = self.request()
        plan = dict(lesson_id='TB-001', source_sha256='1'*64, paragraphs=[{}],
                    units=[dict(source='this text must not leave the planner')])
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(runtime, 'config', return_value=({}, {'shard_chars': 4000}, request, ['TB-001'])), \
             patch.object(runtime, 'protected_keys', return_value=(b'source', b'')), \
             patch.object(runtime, 'source_plan', return_value=(plan, {})), \
             contextlib.redirect_stdout(io.StringIO()):
            args = argparse.Namespace(settings_sha='2'*64, output=Path(tmp)/'plan.json',
                                      github_output=Path(tmp)/'github')
            runtime.public_plan(args, profile='single_pass')
            self.assertIn('complete_course=false\n', args.github_output.read_text())
            self.assertNotIn(plan['units'][0]['source'], args.output.read_text())
