"""Network-free checks for immutable release transport and public-only payloads."""
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import transport as t
from lecture_crypto import seal
from lecture_pack import make_pack_manifest, seal_part


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


class MemoryBackend:
    def __init__(self):
        self.releases = {}
        self.data = {}
        self.events = []
        self.next_id = 1

    def release(self, tag):
        return self.releases.get(tag)

    def create(self, tag):
        release = {'id': self.next_id, 'tag_name': tag, 'draft': True}
        self.next_id += 1
        self.releases[tag] = release
        self.events.append(('create', tag))
        return release

    def assets(self, release):
        return [dict(row[0]) for (tag, name), row in self.data.items() if tag == release['tag_name']]

    def download(self, asset, destination):
        raw = next(blob for row, blob in self.data.values() if row['id'] == asset['id'])
        with Path(destination).open('xb') as handle:
            handle.write(raw)

    def upload(self, release, path):
        tag, name = release['tag_name'], path.name
        if (tag, name) in self.data:
            raise t.TransportError('exists')
        blob = path.read_bytes()
        asset = {'id': self.next_id, 'name': name, 'size': len(blob),
                 'url': f'https://api.github.com/repos/{t.REPOSITORY}/releases/assets/{self.next_id}',
                 'browser_download_url': f'https://github.com/{t.REPOSITORY}/releases/download/{tag}/{name}'}
        self.next_id += 1
        self.data[(tag, name)] = (asset, blob)
        self.events.append(('upload', name))

    def publish(self, release):
        self.events.append(('publish', release['tag_name']))
        release['draft'] = False


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backend = MemoryBackend()
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def bundle(self, directory=None, ident='TB-001', mutate=None, extra=None, cipher_mutate=False):
        directory = directory or self.root
        directory.mkdir(parents=True, exist_ok=True)
        manifest, blob = seal(bytes(32), b'private synthetic checkpoint', ident, 'checkpoint', t.KEY_ID)
        if cipher_mutate:
            blob = bytes([blob[0] ^ 1]) + blob[1:]
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
            if extra:
                archive.writestr(extra, b'unsafe')
        return path

    def triple(self, directory, ident):
        directory.mkdir(parents=True, exist_ok=True)
        pack_id = 'a'*32
        part, audio = seal_part(bytes(32), b'synthetic audio', ident, pack_id, 0, 0, 44100, 0)
        pack = make_pack_manifest(ident, pack_id, [part], audio)
        content, blob = seal(bytes(32), b'{"synthetic":true}', ident, 'private-metadata', t.KEY_ID)
        descriptor = dict(version='integrated-lecture-output/v2', lesson_id=ident, audio=pack, content=content)
        (directory / f'{ident}.audio.{pack_id}.bin').write_bytes(audio)
        (directory / f'{ident}.content.{content["object_id"]}.bin').write_bytes(blob)
        path = directory / f'{ident}.descriptor.json'
        path.write_bytes(encoded(descriptor))
        return path

    def course(self, course='TB', profile='eager', directory=None):
        directory = directory or self.root / 'final'
        tag = t.tag_for(course, True, profile=profile)
        lessons = []
        for ident in t.course_ids(course):
            path = directory / f'{ident}.descriptor.json'
            if not path.is_file():
                self.triple(directory, ident)
            lessons.append(dict(id=ident,
                                descriptor_url=f'https://github.com/{t.REPOSITORY}/releases/download/{tag}/{path.name}',
                                sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        (directory / 'index.json').write_bytes(encoded(dict(version='integrated-lecture-index/v2',
                                                          release_tag=tag, count=len(lessons), lessons=lessons)))
        return directory

    def test_keyless_guard_checks_name_even_if_empty(self):
        for name in ('SOURCE_KEY_B64', 'INTEGRATED_MEDIA_KEY', 'KEY_SOURCE'):
            with self.subTest(name=name), patch.dict(os.environ, {name: ''}):
                with self.assertRaisesRegex(t.TransportError, 'environment_present'):
                    t.prepare('TB', self.backend)
        self.assertEqual(self.backend.events, [])

    def test_repository_and_course_boundaries(self):
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'someone/else'}):
            with self.assertRaises(t.TransportError):
                t.prepare('TB', self.backend)
        for ident in ('TB-026', 'MS-167', '../TB-001', 'TB001'):
            with self.subTest(ident=ident), self.assertRaises(t.TransportError):
                t.download_lesson(ident, self.root / 'download', backend=self.backend)
        with self.assertRaises(t.TransportError):
            t.validate_bundle(self.bundle(), 'RL')

    def test_valid_cipher_bundle_upload_is_idempotent(self):
        self.bundle()
        self.assertEqual(t.upload_directory(self.root, 'TB', self.backend)['uploaded'], 1)
        self.assertEqual(t.upload_directory(self.root, 'TB', self.backend)['skipped'], 1)
        self.assertEqual(sum(event[0] == 'upload' for event in self.backend.events), 1)

    def test_collision_never_clobbers(self):
        path = self.bundle()
        t.upload_directory(self.root, 'TB', self.backend)
        old = next(iter(self.backend.data.values()))[1]
        self.bundle()  # New random AEAD envelope has the same immutable name.
        self.assertNotEqual(path.read_bytes(), old)
        with self.assertRaisesRegex(t.TransportError, 'immutable_asset_collision'):
            t.upload_directory(self.root, 'TB', self.backend)
        self.assertEqual(next(iter(self.backend.data.values()))[1], old)

    def test_raw_or_path_traversal_zip_members_rejected_before_network(self):
        for name in ('final.wav', '../private.json', 'source.txt', 'sub/report.json'):
            with self.subTest(name=name):
                self.bundle(extra=name)
                with self.assertRaisesRegex(t.TransportError, 'unapproved_bundle_member'):
                    t.upload_directory(self.root, 'TB', self.backend)
        self.assertEqual(self.backend.events, [])

    def test_public_extra_fields_and_bad_pin_rejected(self):
        for mutate in (lambda e, r: r.update(source='private'),
                       lambda e, r: e.update(receipt='private'),
                       lambda e, r: e.update(plan_sha256='4'*64),
                       lambda e, r: r.update(pending=1),
                       lambda e, r: r.update(run_id='456')):
            with self.subTest(mutate=mutate), self.assertRaises(t.TransportError):
                t.validate_bundle(self.bundle(mutate=mutate), 'TB')

    def test_cipher_digest_checked(self):
        with self.assertRaisesRegex(t.TransportError, 'cipher_hash_mismatch'):
            t.validate_bundle(self.bundle(cipher_mutate=True), 'TB')

    def test_encrypted_blocked_units_can_resume(self):
        path = self.bundle(mutate=lambda e, r: r.update(passed=0, blocked=1))
        self.assertEqual(t.validate_bundle(path, 'TB')['blocked'], 1)

    def test_restored_units_need_not_be_uploaded_again(self):
        path = self.bundle()
        with zipfile.ZipFile(path) as archive:
            report = archive.read('report.json')
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('report.json', report)
        self.assertEqual(t.validate_bundle(path, 'TB')['passed'], 1)

    def test_download_validates_and_keeps_archives_with_no_clobber(self):
        source = self.root / 'uploads'
        original = self.bundle(source)
        self.bundle(source, ident='TB-002')
        t.upload_directory(source, 'TB', self.backend)
        destination = self.root / 'resume'
        self.assertEqual(t.download_lesson('TB-001', destination, backend=self.backend)['downloaded'], 1)
        self.assertEqual((destination / original.name).read_bytes(), original.read_bytes())
        self.assertEqual(len(list(destination.iterdir())), 1)
        self.assertEqual(t.download_lesson('TB-001', destination, backend=self.backend)['skipped'], 1)
        self.assertFalse(any(p.suffix == '.json' for p in destination.iterdir()))

    def test_missing_staging_release_is_normal(self):
        result = t.download_lesson('MS-166', self.root / 'resume', backend=self.backend)
        self.assertEqual((result['status'], result['downloaded']), ('no_checkpoints', 0))

    def test_download_tampering_does_not_commit_output(self):
        source = self.root / 'uploads'
        path = self.bundle(source)
        t.upload_directory(source, 'TB', self.backend)
        key = (t.tag_for('TB'), path.name)
        row, raw = self.backend.data[key]
        self.backend.data[key] = (row, b'x' * len(raw))
        destination = self.root / 'resume'
        with self.assertRaises(t.TransportError):
            t.download_lesson('TB-001', destination, backend=self.backend)
        self.assertEqual(list(destination.iterdir()), [])

    def test_partial_publish_is_draft_and_index_forbidden(self):
        directory = self.root / 'partial'
        self.triple(directory, 'TB-001')
        result = t.publish_directory(directory, 'TB', partial=True, backend=self.backend)
        self.assertEqual(result['uploaded'], 3)
        self.assertFalse(result['activated_index'])
        self.assertTrue(self.backend.release(t.tag_for('TB', True))['draft'])
        (directory / 'index.json').write_text('{}')
        with self.assertRaisesRegex(t.TransportError, 'partial_requires'):
            t.publish_directory(directory, 'TB', partial=True, backend=self.backend)

    def test_full_publish_requires_all_lessons_and_uploads_index_last(self):
        partial = self.root / 'partial'
        self.triple(partial, 'TB-001')
        with self.assertRaisesRegex(t.TransportError, 'incomplete_course'):
            t.publish_directory(partial, 'TB', backend=self.backend)
        self.assertEqual(self.backend.events, [])
        directory = self.course()
        result = t.publish_directory(directory, 'TB', backend=self.backend)
        self.assertEqual(result['uploaded'], 76)
        self.assertEqual(self.backend.events[-2], ('upload', 'index.json'))
        self.assertEqual(self.backend.events[-1][0], 'publish')
        self.assertFalse(self.backend.release(t.tag_for('TB', True))['draft'])
        self.assertEqual(t.publish_directory(directory, 'TB', backend=self.backend)['skipped'], 76)

    def test_immutable_index_collision_detected_before_uploads(self):
        directory = self.course()
        release = self.backend.create(t.tag_for('TB', True))
        self.backend.upload(release, directory / 'index.json')
        key = (release['tag_name'], 'index.json')
        row, raw = self.backend.data[key]
        self.backend.data[key] = (row, b'x' * len(raw))
        events_before = list(self.backend.events)
        with self.assertRaisesRegex(t.TransportError, 'immutable_asset_collision'):
            t.publish_directory(directory, 'TB', backend=self.backend)
        self.assertEqual(events_before, self.backend.events)

    def test_index_scope_and_raw_final_files_rejected(self):
        directory = self.course()
        index_path = directory / 'index.json'
        original = index_path.read_bytes()
        index = json.loads(original)
        index['lessons'][0]['descriptor_url'] = 'http://evil.test/private'
        index_path.write_bytes(encoded(index))
        with self.assertRaisesRegex(t.TransportError, 'asset_url'):
            t.validate_final_directory(directory, 'TB')
        index_path.write_bytes(original)
        (directory / 'source.txt').write_text('private')
        with self.assertRaises(t.TransportError):
            t.validate_final_directory(directory, 'TB')

    def test_single_pass_checkpoint_drafts_are_isolated_by_course_and_profile(self):
        for course in t.COURSES:
            source = self.root / course
            original = self.bundle(source, ident=course + '-001')
            tag = f'bora-fp32-{course.lower()}-single-pass-staging-20261114'
            self.assertEqual(t.prepare(course, self.backend, profile='single_pass')['tag'], tag)
            self.assertEqual(t.upload_directory(source, course, self.backend, profile='single_pass')['uploaded'], 1)
            self.assertEqual(t.download_lesson(course + '-001', self.root / 'old' / course,
                                              backend=self.backend)['downloaded'], 0)
            recovered = self.root / 'new' / course
            self.assertEqual(t.download_lesson(course + '-001', recovered,
                                              backend=self.backend, profile='single_pass')['downloaded'], 1)
            self.assertEqual((recovered / original.name).read_bytes(), original.read_bytes())
            self.backend.release(tag)['draft'] = False
            with self.assertRaisesRegex(t.TransportError, 'single_pass_staging_must_be_draft'):
                t.prepare(course, self.backend, profile='single_pass')

    def test_single_pass_partial_draft_resume_then_publish_complete_course(self):
        source = self.root / 'uploads'
        descriptor = self.triple(source, 'TB-001')
        t.publish_directory(source, 'TB', partial=True, backend=self.backend)
        recovered = self.root / 'collected'
        self.assertEqual(t.collect_final('TB', recovered, 'TB-001', self.backend,
                                         profile='single_pass')['collected'], [])
        result = t.publish_directory(source, 'TB', partial=True, backend=self.backend, profile='single_pass')
        final_tag = 'bora-fp32-tb-single-pass-v1-20261114'
        self.assertEqual(result['tag'], final_tag)
        self.assertFalse(result['activated_index'])
        self.assertTrue(self.backend.release(final_tag)['draft'])
        for _ in range(2):
            self.assertEqual(t.collect_final('TB', recovered, 'TB-001', self.backend,
                                             profile='single_pass')['collected'], ['TB-001'])
        self.assertEqual((recovered / descriptor.name).read_bytes(), descriptor.read_bytes())
        events = list(self.backend.events)
        with self.assertRaisesRegex(t.TransportError, 'incomplete_course'):
            t.publish_directory(recovered, 'TB', backend=self.backend, profile='single_pass')
        self.assertEqual(self.backend.events, events)
        full = self.course(profile='single_pass', directory=recovered)
        with self.assertRaisesRegex(t.TransportError, 'index_scope'):
            t.validate_final_directory(full, 'TB')
        result = t.publish_directory(full, 'TB', backend=self.backend, profile='single_pass')
        self.assertEqual((result['uploaded'], result['skipped']), (73, 3))
        self.assertEqual(self.backend.events[-2:], [('upload', 'index.json'), ('publish', final_tag)])
        self.assertFalse(self.backend.release(final_tag)['draft'])
        with self.assertRaisesRegex(t.TransportError, 'course_already_finalized'):
            t.publish_directory(source, 'TB', partial=True, backend=self.backend, profile='single_pass')

    def test_single_pass_collect_validates_ciphertext_before_reuse(self):
        source = self.root / 'source'
        self.triple(source, 'MS-166')
        result = t.publish_directory(source, 'MS', partial=True, backend=self.backend, profile='single_pass')
        key = (result['tag'], next(source.glob('*.audio.*.bin')).name)
        row, raw = self.backend.data[key]
        self.backend.data[key] = (row, b'x' * len(raw))
        recovered = self.root / 'recovered'
        with self.assertRaisesRegex(t.TransportError, 'invalid_lesson_cipher_triple'):
            t.collect_final('MS', recovered, 'MS-166', self.backend, profile='single_pass')
        self.assertEqual(list(recovered.iterdir()), [])

    def test_single_pass_mismatched_staged_descriptor_cannot_upload_new_objects(self):
        source = self.root / 'source'
        self.triple(source, 'TB-001')
        t.publish_directory(source, 'TB', partial=True, backend=self.backend, profile='single_pass')
        changed = self.root / 'changed'
        self.triple(changed, 'TB-001')
        events = list(self.backend.events)
        with self.assertRaisesRegex(t.TransportError, 'immutable_asset_collision'):
            t.publish_directory(changed, 'TB', partial=True, backend=self.backend, profile='single_pass')
        self.assertEqual(self.backend.events, events)

    def test_remote_asset_url_is_never_trusted(self):
        self.bundle()
        t.upload_directory(self.root, 'TB', self.backend)
        row = next(iter(self.backend.data.values()))[0]
        row['url'] = 'https://api.github.com.evil.test/private'
        with self.assertRaisesRegex(t.TransportError, 'asset_url'):
            t.upload_directory(self.root, 'TB', self.backend)

    def test_real_draft_untagged_asset_url_is_bound_to_release(self):
        self.bundle()
        release = self.backend.create(t.tag_for('TB'))
        slug = 'untagged-' + 'a' * 20
        release['html_url'] = f'https://github.com/{t.REPOSITORY}/releases/tag/{slug}'
        self.backend.upload(release, next(self.root.glob('*.zip')))
        row = next(iter(self.backend.data.values()))[0]
        original = row['browser_download_url']
        row['browser_download_url'] = original.replace(
            '/releases/download/' + release['tag_name'] + '/',
            '/releases/download/' + slug + '/')
        self.assertEqual(t.upload_directory(self.root, 'TB', self.backend)['skipped'], 1)
        release['html_url'] = f'https://github.com/{t.REPOSITORY}/releases/tag/untagged-' + 'b' * 20
        with self.assertRaisesRegex(t.TransportError, 'asset_url'):
            t.upload_directory(self.root, 'TB', self.backend)
        release['html_url'] = 'https://github.com.evil.test/release'
        with self.assertRaisesRegex(t.TransportError, 'release_draft_url'):
            t.upload_directory(self.root, 'TB', self.backend)
        release['html_url'] = f'https://github.com/{t.REPOSITORY}/releases/tag/{slug}'
        release['draft'] = False
        with self.assertRaisesRegex(t.TransportError, 'asset_url'):
            t.upload_directory(self.root, 'TB', self.backend)

    def test_remote_upload_url_must_be_github(self):
        self.bundle()
        release = self.backend.create(t.tag_for('TB'))
        release['upload_url'] = 'https://evil.test/upload'
        with self.assertRaisesRegex(t.TransportError, 'release_upload_url'):
            t.upload_directory(self.root, 'TB', self.backend)

    def test_symlink_and_duplicate_json_rejected(self):
        original = self.bundle(self.root / 'original')
        directory = self.root / 'symlinks'
        directory.mkdir()
        (directory / original.name).symlink_to(original)
        with self.assertRaises(t.TransportError):
            t.upload_directory(directory, 'TB', self.backend)
        with self.assertRaisesRegex(t.TransportError, 'duplicate_json_key'):
            t._json(b'{"a":1,"a":2}')

    def test_gh_backend_forwards_only_github_token_and_no_clobber(self):
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'safe-token', 'GH_TOKEN': 'wrong-token',
                                    'SOME_SECRET': 'not-forwarded', 'PATH': '/bin'}), \
             patch.object(t.shutil, 'which', return_value='/bin/gh'), \
             patch.object(t.subprocess, 'run') as run:
            run.return_value = type('Result', (), {'returncode': 0, 'stdout': b'{}', 'stderr': b''})()
            backend = t.GitHubBackend()
            backend.upload({'tag_name': t.tag_for('TB')}, self.root / 'cipher.zip')
            args, kwargs = run.call_args
            self.assertNotIn('--clobber', args[0])
            self.assertEqual(kwargs['env']['GITHUB_TOKEN'], 'safe-token')
            self.assertNotIn('GH_TOKEN', kwargs['env'])
            self.assertNotIn('SOME_SECRET', kwargs['env'])
            self.assertEqual(kwargs['env']['GH_HOST'], 'github.com')


if __name__ == '__main__':
    unittest.main()

