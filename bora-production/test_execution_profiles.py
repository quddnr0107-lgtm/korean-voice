"""Exercise fixed requests and workflow authorization without protected keys."""
import unittest
from unittest.mock import patch

import execution_profiles as profiles
import runtime


class ExecutionProfileTests(unittest.TestCase):
    def test_profiles_share_campaign_and_recipe_but_not_the_request(self):
        campaign, recipe, legacy, legacy_ids = runtime.config()
        compiled_campaign, compiled_recipe, compiled, compiled_ids = runtime.config('compiled')
        self.assertEqual((compiled_campaign, compiled_recipe), (campaign, recipe))
        expected_ids = [f"{legacy['course']}-{index:03d}"
                        for index in range(1, runtime.LIMITS[legacy['course']] + 1)]
        self.assertEqual(legacy_ids, sorted(legacy.get('lesson_ids', expected_ids)))
        self.assertEqual(compiled_ids, ['TB-002'])
        self.assertNotIn('execution_profile', legacy)
        self.assertEqual(compiled['execution_profile'], profiles.PROFILE_ID)
        self.assertNotEqual(compiled['request_id'], legacy['request_id'])
        self.assertNotEqual(runtime.identity(compiled), runtime.identity(legacy))
        self.assertEqual(runtime.execution_shard_chars(recipe, compiled), 2000)

    def test_compiled_request_rejects_expansion_retry_and_type_coercion(self):
        _, _, request, _ = runtime.config('compiled')
        changes = (dict(lesson_ids=['TB-001']), dict(lesson_ids=['TB-001', 'TB-002']),
            dict(course='RL'), dict(retry_blocked=True), dict(retry_blocked=0),
            dict(execution_shard_chars=2000.0), dict(execution_shard_chars=4000),
            dict(execution_profile='eager'), dict(selected_shards=None),
            dict(campaign_sha256='0' * 64), dict(request_id='another-request'))
        for change in changes:
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'compiled_request_scope'):
                profiles.validate_request(dict(request, **change), 'compiled')

    def test_fixed_workflows_cross_reject_before_namespace_or_key_access(self):
        # The sentinel stops the guard before reading or consuming any keys.
        class NamespaceReached(Exception):
            pass

        for actual in ('bora-fp32-production.yml', 'bora-fp32-compiled-pilot.yml'):
            for profile in ('eager', 'compiled'):
                expected = (actual == 'bora-fp32-production.yml') == (profile == 'eager')
                env = dict(GITHUB_REPOSITORY=runtime.REPO,
                    GITHUB_REF='refs/heads/' + runtime.BRANCH, GITHUB_EVENT_NAME='push',
                    GITHUB_WORKFLOW_REF=f'{runtime.REPO}/.github/workflows/{actual}@refs/heads/{runtime.BRANCH}')
                with self.subTest(actual=actual, profile=profile), patch.object(runtime.os, 'environ', env), \
                     patch.object(runtime.socket, 'if_nameindex', return_value=[(1, 'lo')]), \
                     patch.object(runtime.Path, 'read_text', return_value=''), \
                     patch.object(runtime.os, 'readlink', return_value='net:[1]'), \
                     patch.object(runtime, 'validate_snapshot', side_effect=NamespaceReached) as guard:
                    if expected:
                        with self.assertRaises(NamespaceReached):
                            runtime.protected_keys(**({'profile': profile} if profile == 'compiled' else {}))
                        guard.assert_called_once()
                    else:
                        with self.assertRaisesRegex(ValueError, 'protected_workflow_context'):
                            runtime.protected_keys(**({'profile': profile} if profile == 'compiled' else {}))
                        guard.assert_not_called()


if __name__ == '__main__':
    unittest.main()
