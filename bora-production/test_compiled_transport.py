"""Network-free boundary tests for the isolated, ciphertext-only TB-002 pilot."""
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from lecture_crypto import seal
from test_transport import MemoryBackend, encoded
import transport as t


class RecordingBackend(MemoryBackend):
    def __init__(self):
        super().__init__()
        self.lookups = []

    def release(self, tag):
        self.lookups.append(tag)
        return super().release(tag)


class CompiledTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backend = RecordingBackend()
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def bundle(self, directory=None, ident='TB-002', mutate=None):
        directory = directory or self.root
        directory.mkdir(parents=True, exist_ok=True)
        manifest, blob = seal(bytes(32), b'synthetic private checkpoint', ident, 'checkpoint', t.KEY_ID)
        envelope = dict(version='bora-fp32-unit-envelope/v1', lesson_id=ident, unit_index=0,
                        plan_sha256='1'*64, recipe_sha256='2'*64, render_settings_sha256='3'*64,
                        manifest=manifest, cipher_b64=base64.b64encode(blob).decode())
        report = dict(schema='bora-fp32-shard-report/v1', lesson_id=ident, shard=0, start=0, end=1,
                      passed=1, blocked=0, pending=0, timed_out=False, recipe_sha256='2'*64,
                      plan_sha256='1'*64, render_settings_sha256='3'*64, run_id='123', run_attempt='1')
        if mutate:
            mutate(envelope, report)
        path = directory / f'{ident}-s000-r123-a1.zip'
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('report.json', encoded(report))
            archive.writestr('unit-00000.sealed.json', encoded(envelope))
        return path

    def test_legacy_tags_and_explicit_default_are_identical(self):
        for course in t.COURSES:
            for final in (False, True):
                with self.subTest(course=course, final=final):
                    expected = f'bora-fp32-{course.lower()}-{"v1" if final else "staging"}-20261112'
                    self.assertEqual(t.tag_for(course, final), expected)
                    self.assertEqual(t.tag_for(course, final, profile='eager'), expected)
        self.assertEqual(t.tag_for('TB', profile='compiled'),
                         'bora-fp32-tb-compiled-pilot-staging-20261114')

    def test_compiled_tag_has_no_other_course_or_final_variant(self):
        for course in ('RL', 'DR', 'MS'):
            with self.subTest(course=course), self.assertRaisesRegex(t.TransportError, 'compiled_course_scope'):
                t.tag_for(course, profile='compiled')
        with self.assertRaisesRegex(t.TransportError, 'compiled_final_forbidden'):
            t.tag_for('TB', True, profile='compiled')
        with self.assertRaisesRegex(t.TransportError, 'execution_profile_scope'):
            t.tag_for('TB', profile='unknown')

    def test_prepare_creates_only_isolated_draft(self):
        result = t.prepare('TB', self.backend, profile='compiled')
        self.assertEqual(result['tag'], t.COMPILED_STAGING_TAG)
        self.assertEqual(self.backend.lookups, [t.COMPILED_STAGING_TAG])
        self.assertEqual(self.backend.events, [('create', t.COMPILED_STAGING_TAG)])
        self.assertTrue(self.backend.releases[t.COMPILED_STAGING_TAG]['draft'])

    def test_compiled_course_and_lesson_scope_fail_before_backend(self):
        for course in ('RL', 'DR', 'MS'):
            with self.subTest(course=course), self.assertRaises(t.TransportError):
                t.prepare(course, self.backend, profile='compiled')
        for ident in ('TB-001', 'TB-003', 'TB-025', 'RL-001', 'MS-166'):
            with self.subTest(ident=ident), self.assertRaises(t.TransportError):
                t.download_lesson(ident, self.root / 'resume', backend=self.backend, profile='compiled')
        self.assertEqual(self.backend.lookups, [])
        self.assertEqual(self.backend.events, [])
        self.assertFalse((self.root / 'resume').exists())

    def test_every_upload_bundle_must_be_tb002_before_any_mutation(self):
        self.bundle()
        self.bundle(ident='TB-001')
        with self.assertRaisesRegex(t.TransportError, 'compiled_lesson_scope'):
            t.upload_directory(self.root, 'TB', self.backend, profile='compiled')
        self.assertEqual(self.backend.lookups, [])
        self.assertEqual(self.backend.events, [])

    def test_report_scope_and_settings_pin_remain_required(self):
        for mutate, error in ((lambda e, r: r.update(lesson_id='TB-001'), 'report_scope'),
                              (lambda e, r: r.update(render_settings_sha256='4'*64), 'envelope_pin'),
                              (lambda e, r: r.update(render_settings_sha256='invalid'), 'report_pin'),
                              (lambda e, r: e.update(lesson_id='TB-001'), 'envelope_scope')):
            with self.subTest(error=error), self.assertRaisesRegex(t.TransportError, error):
                t.validate_bundle(self.bundle(mutate=mutate), 'TB', profile='compiled')

    def test_default_and_compiled_transport_use_disjoint_releases(self):
        eager = self.bundle(self.root / 'eager')
        compiled = self.bundle(self.root / 'compiled')
        self.assertNotEqual(eager.read_bytes(), compiled.read_bytes())
        t.upload_directory(eager.parent, 'TB', self.backend)
        t.upload_directory(compiled.parent, 'TB', self.backend, profile='compiled')
        self.assertEqual(set(self.backend.releases), {t.tag_for('TB'), t.COMPILED_STAGING_TAG})
        self.backend.lookups.clear()
        out = self.root / 'compiled-resume'
        self.assertEqual(t.download_lesson('TB-002', out, backend=self.backend,
                                          profile='compiled')['downloaded'], 1)
        self.assertEqual((out / compiled.name).read_bytes(), compiled.read_bytes())
        self.assertEqual(self.backend.lookups, [t.COMPILED_STAGING_TAG])
        eager_out = self.root / 'eager-resume'
        t.download_lesson('TB-002', eager_out, backend=self.backend)
        self.assertEqual((eager_out / eager.name).read_bytes(), eager.read_bytes())
        self.assertFalse(any(event[0] == 'publish' for event in self.backend.events))

    def test_missing_compiled_release_never_falls_back_to_eager(self):
        path = self.bundle()
        t.upload_directory(path.parent, 'TB', self.backend)
        self.backend.lookups.clear()
        result = t.download_lesson('TB-002', self.root / 'resume', backend=self.backend, profile='compiled')
        self.assertEqual(result['status'], 'no_checkpoints')
        self.assertEqual(self.backend.lookups, [t.COMPILED_STAGING_TAG])
        self.assertEqual(list((self.root / 'resume').iterdir()), [])

    def test_compiled_upload_idempotence_and_local_collision_guard(self):
        self.bundle()
        self.assertEqual(t.upload_directory(self.root, 'TB', self.backend, profile='compiled')['uploaded'], 1)
        self.assertEqual(t.upload_directory(self.root, 'TB', self.backend, profile='compiled')['skipped'], 1)
        out = self.root / 'resume'
        out.mkdir()
        self.bundle(out)
        with self.assertRaisesRegex(t.TransportError, 'immutable_asset_collision'):
            t.download_lesson('TB-002', out, backend=self.backend, profile='compiled')

    def test_compiled_download_rejects_mislabeled_report_without_committing(self):
        bad = self.bundle(mutate=lambda e, r: r.update(lesson_id='TB-001'))
        release = self.backend.create(t.COMPILED_STAGING_TAG)
        self.backend.upload(release, bad)
        out = self.root / 'resume'
        with self.assertRaisesRegex(t.TransportError, 'report_scope'):
            t.download_lesson('TB-002', out, backend=self.backend, profile='compiled')
        self.assertEqual(list(out.iterdir()), [])

    def test_compiled_release_rejects_unrelated_remote_assets(self):
        path = self.bundle(self.root / 'uploads')
        release = self.backend.create(t.COMPILED_STAGING_TAG)
        wrong = self.root / 'index.json'
        wrong.write_text('{}')
        self.backend.upload(release, wrong)
        with self.assertRaisesRegex(t.TransportError, 'compiled_staging_asset_scope'):
            t.download_lesson('TB-002', self.root / 'resume', backend=self.backend, profile='compiled')
        wrong.unlink()
        with self.assertRaisesRegex(t.TransportError, 'compiled_staging_asset_scope'):
            t.upload_directory(path.parent, 'TB', self.backend, profile='compiled')

    def test_compiled_release_rejects_tb001_remote_bundle(self):
        wrong = self.bundle(ident='TB-001')
        release = self.backend.create(t.COMPILED_STAGING_TAG)
        self.backend.upload(release, wrong)
        with self.assertRaisesRegex(t.TransportError, 'compiled_staging_asset_scope'):
            t.download_lesson('TB-002', self.root / 'resume', backend=self.backend, profile='compiled')

    def test_published_compiled_release_is_rejected(self):
        self.bundle()
        self.backend.create(t.COMPILED_STAGING_TAG)['draft'] = False
        for action in (lambda: t.prepare('TB', self.backend, profile='compiled'),
                       lambda: t.upload_directory(self.root, 'TB', self.backend, profile='compiled'),
                       lambda: t.download_lesson('TB-002', self.root / 'resume',
                                                  backend=self.backend, profile='compiled')):
            with self.subTest(action=action), self.assertRaisesRegex(t.TransportError, 'compiled_release_must_be_draft'):
                action()
        self.assertEqual(self.backend.events, [('create', t.COMPILED_STAGING_TAG)])

    def test_compiled_cannot_publish_partial_or_full(self):
        for partial in (False, True):
            with self.subTest(partial=partial), self.assertRaisesRegex(t.TransportError, 'compiled_final_forbidden'):
                t.publish_directory(self.root, 'TB', partial, self.backend, profile='compiled')
        self.assertEqual(self.backend.lookups, [])
        self.assertEqual(self.backend.events, [])

    def test_compiled_draft_asset_slug_remains_bound_to_exact_release(self):
        path = self.bundle()
        release = self.backend.create(t.COMPILED_STAGING_TAG)
        slug = 'untagged-' + 'a' * 20
        release['html_url'] = f'https://github.com/{t.REPOSITORY}/releases/tag/{slug}'
        self.backend.upload(release, path)
        row = self.backend.data[(t.COMPILED_STAGING_TAG, path.name)][0]
        row['browser_download_url'] = f'https://github.com/{t.REPOSITORY}/releases/download/{slug}/{path.name}'
        self.assertEqual(t.upload_directory(self.root, 'TB', self.backend, profile='compiled')['skipped'], 1)
        for bad_url, error in ((f'https://github.com/{t.REPOSITORY}/releases/tag/untagged-' + 'b'*20, 'asset_url'),
                               ('https://github.com.evil.test/release', 'release_draft_url'),
                               (f'https://github.com/{t.REPOSITORY}/releases/tag/{slug}?x=1', 'release_draft_url'),
                               (f'https://github.com/{t.REPOSITORY}/releases/tag/{t.tag_for("TB")}', 'release_draft_url')):
            release['html_url'] = bad_url
            with self.subTest(url=bad_url), self.assertRaisesRegex(t.TransportError, error):
                t.upload_directory(self.root, 'TB', self.backend, profile='compiled')

    def test_compiled_keeps_keyless_and_repository_environment_guards(self):
        for env in ({'SOURCE_KEY_B64': ''}, {'INTEGRATED_MEDIA_KEY': 'private'},
                    {'GITHUB_REPOSITORY': 'someone/else'}):
            with self.subTest(env=env), patch.dict(os.environ, env), self.assertRaises(t.TransportError):
                t.prepare('TB', self.backend, profile='compiled')
        self.assertEqual(self.backend.lookups, [])

    def test_cli_forwards_explicit_profile_and_preserves_default(self):
        cases = ((['prepare', '--course', 'TB'], 'prepare', ('TB',)),
                 (['upload', '--course', 'TB', '--directory', '/cipher'], 'upload_directory', (Path('/cipher'), 'TB')),
                 (['download', '--lesson', 'TB-002', '--out', '/resume'], 'download_lesson',
                  ('TB-002', Path('/resume'), None)))
        for argv, function, expected in cases:
            for profile in ('eager', 'compiled'):
                with self.subTest(function=function, profile=profile), \
                     patch.object(t, function, return_value={'status': 'test'}) as call, \
                     contextlib.redirect_stdout(io.StringIO()):
                    args = argv if profile == 'eager' else argv + ['--profile', profile]
                    self.assertEqual(t.main(args), 0)
                    call.assert_called_once_with(*expected, profile=profile)

    def test_cli_rejects_unknown_profile_and_compiled_publication(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            t.main(['prepare', '--course', 'TB', '--profile', 'unknown'])
        self.assertEqual(error.exception.code, 2)
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            self.assertEqual(t.main(['publish', '--course', 'TB', '--directory', str(self.root),
                                     '--profile', 'compiled']), 2)
        self.assertEqual(json.loads(output.getvalue())['reason'], 'compiled_final_forbidden')


if __name__ == '__main__':
    unittest.main()
