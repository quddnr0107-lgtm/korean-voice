"""Synthetic public bytes only; never download real models or handle keys."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import public_asset_cache as c


class PublicAssetCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cache, self.runtime = self.root/'cache', self.root/'runtime'
        self.payloads = {'qwen/config.json': b'{"public":true}',
                         'qwen/sub/weights.bin': b'PUBLIC_TTS_WEIGHTS',
                         'asr/model.bin': b'PUBLIC_ASR_WEIGHTS'}
        self.manifest = {'models': {name: {'revision': 'a'*40, 'files': {}}
                                   for name in ('qwen', 'asr')}}
        for path, data in self.payloads.items():
            name, filename = path.split('/', 1)
            self.manifest['models'][name]['files'][filename] = {
                'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        self.downloads = 0

    def tearDown(self):
        self.temp.cleanup()

    def downloader(self, root, manifest):
        self.downloads += 1
        for path, data in self.payloads.items():
            target = root/path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        # Neither cached HF metadata nor a downloaded token is copied to cache.
        (root/'qwen/.cache').mkdir()
        (root/'qwen/.cache/token').write_text('SYNTHETIC_NOT_A_TOKEN')

    def prepare(self):
        return c.prepare(self.cache, self.runtime, self.manifest, self.downloader)

    def test_cold_then_warm_preserves_identical_public_bytes(self):
        self.assertTrue(self.prepare())
        c.verify(self.runtime, self.manifest, runtime=True)
        self.assertFalse(self.prepare())
        self.assertEqual(self.downloads, 1)
        c.verify(self.cache, self.manifest)
        for path, data in self.payloads.items():
            self.assertEqual((self.runtime/path).read_bytes(), data)
        self.assertFalse((self.cache/'qwen/.cache').exists())

    def test_same_size_corruption_downloads_once_and_ignores_forged_hf_metadata(self):
        self.prepare()
        target = self.cache/'asr/model.bin'
        target.write_bytes(b'X'*target.stat().st_size)
        fake = self.cache/'asr/.cache/huggingface/download/model.bin.metadata'
        fake.parent.mkdir(parents=True)
        fake.write_text('a'*40+'\n'+c.digest(target)+'\n0\n')
        self.assertTrue(self.prepare())
        self.assertEqual(self.downloads, 2)
        self.assertEqual(target.read_bytes(), self.payloads['asr/model.bin'])
        c.verify(self.runtime, self.manifest, runtime=True)

    def test_bad_fresh_download_fails_closed_without_retry(self):
        def bad(root, manifest):
            self.downloader(root, manifest)
            (root/'asr/model.bin').write_bytes(b'BAD')
        with self.assertRaises(c.CacheError):
            c.prepare(self.cache, self.runtime, self.manifest, bad)
        self.assertEqual(self.downloads, 1)
        self.assertFalse(self.runtime.exists())

    def test_symlinked_cache_leaf_is_unlinked_without_touching_target(self):
        outside = self.root/'outside'; outside.mkdir()
        (outside/'keep').write_bytes(b'KEEP')
        self.cache.symlink_to(outside, target_is_directory=True)
        self.prepare()
        self.assertEqual((outside/'keep').read_bytes(), b'KEEP')
        self.assertFalse(self.cache.is_symlink())

    def test_symlinked_parent_and_runtime_asset_rejected(self):
        real = self.root/'real'; real.mkdir()
        link = self.root/'link'; link.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(c.CacheError, 'asset_symlink'):
            c.prepare(link/'cache', self.runtime, self.manifest, self.downloader)
        self.assertEqual(self.downloads, 0)
        self.prepare()
        target = self.runtime/'asr/model.bin'
        target.unlink(); target.symlink_to(self.cache/'asr/model.bin')
        with self.assertRaisesRegex(c.CacheError, 'asset_symlink'):
            c.verify(self.runtime, self.manifest, runtime=True)

    def test_extra_file_is_removed_by_fresh_download(self):
        self.prepare()
        (self.cache/'private.wav').write_bytes(b'UNEXPECTED_SYNTHETIC_BYTES')
        self.assertTrue(self.prepare())
        self.assertFalse((self.cache/'private.wav').exists())

    def test_runtime_metadata_tampering_fails_before_secret_step(self):
        self.prepare()
        meta = self.runtime/'asr/.cache/huggingface/download/model.bin.metadata'
        meta.write_text('b'*40+'\n'+'0'*64+'\n0\n')
        with self.assertRaisesRegex(c.CacheError, 'asset_runtime_metadata'):
            c.verify(self.runtime, self.manifest, runtime=True)

    def test_manifest_requires_complete_pinned_files_and_safe_paths(self):
        manifest = c.load_manifest()
        self.assertEqual(sum(len(m['files']) for m in manifest['models'].values()), 15)
        for mutate in (lambda m: m['models']['qwen']['files'].pop('config.json'),
                       lambda m: m['models']['qwen'].update(revision='b'*40),
                       lambda m: m['models']['asr']['files']['model.bin'].update(bytes=True),
                       lambda m: m['models']['asr']['files']['model.bin'].update(sha256='bad')):
            bad = copy.deepcopy(manifest); mutate(bad)
            path = self.root/'manifest.json'; path.write_text(json.dumps(bad))
            with self.assertRaises(c.CacheError): c.load_manifest(path)

    def test_key_bearing_prepare_and_verify_entrypoint_refused(self):
        with patch.dict(os.environ, ADMIN_LAW_PREMIUM_MEDIA_KEY_B64='synthetic'):
            with self.assertRaises(RuntimeError): self.prepare()
            with patch('sys.argv', ['public_asset_cache.py', 'verify']):
                with self.assertRaises(RuntimeError): c.main()
        self.assertEqual(self.downloads, 0)

    def test_overlapping_output_paths_rejected(self):
        with self.assertRaisesRegex(c.CacheError, 'asset_paths_overlap'):
            c.prepare(self.cache, self.cache/'runtime', self.manifest, self.downloader)
        self.assertEqual(self.downloads, 0)


if __name__ == '__main__':
    unittest.main()
