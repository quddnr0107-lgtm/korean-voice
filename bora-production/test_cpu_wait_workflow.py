"""Check that tested CPU wait hints reach the unchanged offline model steps."""
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

import cpu_wait_policy as policy


WORKFLOW = Path(__file__).resolve().parents[1]/'.github/workflows/bora-fp32-production.yml'
WAIT_HINTS = {'OMP_WAIT_POLICY': 'PASSIVE', 'GOMP_SPINCOUNT': '0', 'KMP_BLOCKTIME': '0'}
NAMESPACE = 'unshare --net --setgid "$(id -g)" --setuid "$(id -u)" -- "$(command -v python)"'


def namespace_commands(text):
    """Associate each explicit sudo environment whitelist with its runtime verb."""
    commands = {}
    current = None
    for line in text.splitlines():
        match = re.search(r'sudo --preserve-env=([A-Z0-9_,]+) \\$', line)
        if match:
            if current is not None:
                raise ValueError('unterminated namespace command')
            names = match[1].split(',')
            if len(names) != len(set(names)):
                raise ValueError('duplicate preserved variable')
            current = (set(names), [])
        if current is not None:
            current[1].append(line)
            match = re.search(r'bora-production/runtime\.py ([a-z]+)\b', line)
            if match:
                if match[1] in commands:
                    raise ValueError('duplicate runtime namespace command')
                commands[match[1]] = (current[0], '\n'.join(current[1]))
                current = None
    if current is not None:
        raise ValueError('unterminated namespace command')
    return commands


class CpuWaitWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text()

    def test_exact_measured_cpu_only_and_keyless_public_exports(self):
        target = 'model name : '+policy.TARGET_MODEL+'\n'
        self.assertEqual(policy.cpu_models(target*4), {policy.TARGET_MODEL})
        selected, lines = policy.environment_lines(target*4, {})
        self.assertEqual(selected, 'passive')
        self.assertEqual(dict(line.split('=', 1) for line in lines.splitlines()),
                         dict(WAIT_HINTS, BORA_CPU_WAIT_POLICY='passive'))
        for cpuinfo in ('', 'unknown', 'processor : 0\nCPU part : 0xd40\n',
                        'model name : Intel(R) Xeon(R) Platinum 8573C\n',
                        'model name : AMD EPYC 7763\n', target+'model name : \n',
                        target+'model name : Intel(R) Xeon(R) Platinum 8573C\n'):
            with self.subTest(cpuinfo=cpuinfo):
                self.assertEqual(policy.environment_lines(cpuinfo, {}),
                                 ('default', 'BORA_CPU_WAIT_POLICY=default\n'))
        for name in policy.PRIVATE_KEYS:
            with self.assertRaisesRegex(RuntimeError, 'requires_keyless_environment'):
                policy.environment_lines(target, {name: 'SYNTHETIC'})
        for name in WAIT_HINTS:
            for inherited in ('', 'ACTIVE', 'PASSIVE'):
                for cpuinfo in (target, ''):
                    parent = {name: inherited, 'PUBLIC_SENTINEL': 'unchanged'}
                    original = dict(parent)
                    self.assertEqual(policy.environment_lines(cpuinfo, parent),
                                     ('inherited', 'BORA_CPU_WAIT_POLICY=inherited\n'))
                    self.assertEqual(parent, original)

    def test_keyless_cpu_selector_precedes_native_imports_and_keeps_four_threads(self):
        match = re.search(r'^env:\n((?:  [^\n]+\n)+)', self.text, re.MULTILINE)
        self.assertIsNotNone(match)
        global_env = dict(re.findall(r"^  ([A-Z0-9_]+): '([^']*)'$", match[1], re.MULTILINE))
        for name, value in {'OMP_NUM_THREADS': '4', 'OPENBLAS_NUM_THREADS': '4'}.items():
            self.assertEqual(global_env.get(name), value)
            self.assertEqual(re.findall(r'^\s+'+name+r':\s*([^\n]+)$', self.text, re.MULTILINE),
                             ["'"+value+"'"])
        for name in WAIT_HINTS:
            self.assertFalse(re.search(r'^\s+'+name+r':', self.text, re.MULTILINE))
        selector = 'run: python bora-production/cpu_wait_policy.py'
        self.assertEqual(self.text.count(selector), 2)
        for job, install_name in (
                ('preflight', 'Install the pinned runtime without source or media keys'),
                ('render', 'Install the checked runtime only for missing units without keys')):
            section = re.search(r'^  '+job+r':\n(.*?)(?=^  [a-z]+:|\Z)', self.text,
                                re.MULTILINE | re.DOTALL)[1]
            self.assertLess(section.index(selector), section.index(install_name))
            step = next(block for block in re.split(r'^      - ', section, flags=re.MULTILINE)
                        if selector in block)
            self.assertNotIn('env:', step)
            self.assertNotIn('secrets.', step)
            if job == 'render':
                self.assertLess(section.index('runtime.py probe'), section.index(selector))
                self.assertIn("if: steps.probe.outputs.needs_synthesis == 'true'", step)

    def test_model_namespaces_preserve_hints_without_expanding_key_scope(self):
        commands = namespace_commands(self.text)
        public = {'BORA_HOST_NETNS', 'BORA_MODEL', 'BORA_ASR', 'OMP_NUM_THREADS',
                  'OPENBLAS_NUM_THREADS', 'ORT_DISABLE_TELEMETRY', 'HF_HUB_DISABLE_TELEMETRY',
                  'DO_NOT_TRACK', 'RUNNER_TEMP'}
        protected = {'ADMIN_LAW_PREMIUM_SOURCE_KEY_B64', 'ADMIN_LAW_PREMIUM_MEDIA_KEY_B64',
                     'GITHUB_REPOSITORY', 'GITHUB_EVENT_NAME', 'GITHUB_REF', 'GITHUB_WORKFLOW_REF',
                     'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT'}
        self.assertEqual(commands['preflight'][0], public | set(WAIT_HINTS))
        self.assertEqual(commands['render'][0], public | protected | set(WAIT_HINTS))
        for verb in ('preflight', 'render'):
            self.assertIn(NAMESPACE, commands[verb][1])
        self.assertEqual(set(commands), {'preflight', 'plan', 'probe', 'render', 'assemble'})
        for verb in ('plan', 'probe', 'assemble'):
            self.assertFalse(commands[verb][0] & set(WAIT_HINTS))
        paths = re.search(r'^    paths:\n((?:      [^\n]+\n)+)', self.text, re.MULTILINE)
        self.assertIsNotNone(paths)
        self.assertEqual(paths[1], "      - 'bora-production/request.json'\n")

    @unittest.skipUnless(os.environ.get('GITHUB_ACTIONS') == 'true', 'GitHub runner namespace contract')
    def test_actual_sudo_namespace_requires_explicit_hint_preservation(self):
        sudo, unshare = shutil.which('sudo'), shutil.which('unshare')
        self.assertTrue(sudo and unshare)
        self.assertNotEqual(os.getuid(), 0)
        public_env = dict(WAIT_HINTS, PATH=os.defpath, OMP_NUM_THREADS='4',
                          OPENBLAS_NUM_THREADS='4', ORT_DISABLE_TELEMETRY='1',
                          HF_HUB_DISABLE_TELEMETRY='1', DO_NOT_TRACK='1')
        # No inherited credentials, source/media keys, model inputs or network use.
        code = ('import json,os,sys; '
                'assert not any(os.environ.get(k) for k in '
                '("ADMIN_LAW_PREMIUM_SOURCE_KEY_B64","ADMIN_LAW_PREMIUM_MEDIA_KEY_B64")); '
                'expected=json.loads('+repr(json.dumps(WAIT_HINTS))+'); '
                'actual={k:os.environ.get(k) for k in expected}; '
                'ok=actual==expected; print("wait_hints_present" if ok else "wait_hints_missing"); '
                'sys.exit(0 if ok else 42)')
        commands = namespace_commands(self.text)
        for verb in ('preflight', 'render'):
            for preserve_hints in (True, False):
                with self.subTest(verb=verb, preserve_hints=preserve_hints):
                    names = commands[verb][0] - (set() if preserve_hints else set(WAIT_HINTS))
                    result = subprocess.run(
                        [sudo, '-n', '--preserve-env='+','.join(sorted(names)), unshare, '--net',
                         '--setgid', str(os.getgid()), '--setuid', str(os.getuid()), '--',
                         sys.executable, '-c', code], env=public_env, capture_output=True,
                        text=True, timeout=20, check=False)
                    self.assertEqual(result.returncode, 0 if preserve_hints else 42,
                                     result.stderr[:1000])
                    self.assertIn('wait_hints_present' if preserve_hints else 'wait_hints_missing',
                                  result.stdout)


if __name__ == '__main__':
    unittest.main()
