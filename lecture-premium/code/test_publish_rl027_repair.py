import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import zipfile

import publish_rl027_repair as release


class ReleaseTests(unittest.TestCase):
    def test_incomplete_or_mismatched_existing_asset_is_never_overwritten(self):
        items = {'index.json': (53, 'sha256:'+'b'*64)}
        entry = dict(name='index.json', size=53, digest='sha256:'+'b'*64, state='uploaded')
        with patch.object(release, 'api', return_value=[entry]):
            self.assertEqual(release.checked_assets(1, items), set(items))
        for bad in [dict(entry, name='unrelated.json'), dict(entry, size=54),
                    dict(entry, state='starter'), dict(entry, digest='sha256:'+'c'*64)]:
            with patch.object(release, 'api', return_value=[bad]), self.assertRaisesRegex(ValueError, 'asset mismatch'):
                release.checked_assets(1, items)

    def test_only_the_pinned_archive_and_four_complete_ciphertext_files_can_be_examined(self):
        with tempfile.TemporaryDirectory() as folder:
            tmp = Path(folder)
            for files, error in [
                (['index.json'], 'artifact count'),
                (['index.json','../leak','a.bin','b.bin'], 'file scope'),
                (['index.json','RL-027.descriptor.json','RL-027.audio.'+'a'*32+'.bin','clear.wav'], 'file scope')]:
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, 'w') as archive:
                    for name in files:
                        archive.writestr(name, b'qa')
                blob = buf.getvalue()
                with self.assertRaisesRegex(ValueError, 'artifact content'):
                    release.unpack_checked(blob, dict(artifact_sha256='f'*64), tmp)
                with self.assertRaisesRegex(ValueError, error):
                    release.unpack_checked(blob, dict(artifact_sha256=release.d.sha(blob)), tmp)

    def test_verified_ciphertext_pack_and_private_metadata_match_pinned_single_lesson_index(self):
        d = release.d
        key = bytes(range(32))
        ident = release.LESSON
        pack_id = 'ab'*16
        part, audio = d.pack.seal_part(key, b'opaque fixture', ident, pack_id, 0, 0, 44100, 0)
        pack = d.pack.make_pack_manifest(ident, pack_id, [part], audio)
        meta, cipher = d.crypto.seal(key, b'fixture metadata', ident, 'private-metadata', d.KEY_ID)
        desc = d.encoded(dict(lesson_id=ident, version='integrated-lecture-output/v2', audio=pack, content=meta))
        url = f'https://github.com/{release.REPO}/releases/download/{release.TAG}/RL-027.descriptor.json'
        index = d.encoded(dict(version='integrated-lecture-index/v2', release_tag=release.TAG, count=1,
                               lessons=[dict(id=ident, sha256=d.sha(desc), descriptor_url=url)]))
        files = {'index.json': index, 'RL-027.descriptor.json': desc,
                 d.media_name(ident, 'audio', pack): audio, d.media_name(ident, 'content', meta): cipher}
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        raw = buf.getvalue()
        cfg = dict(artifact_sha256=d.sha(raw), descriptor_sha256=d.sha(desc), index_sha256=d.sha(index))
        with tempfile.TemporaryDirectory() as tmp:
            result = release.unpack_checked(raw, cfg, Path(tmp))
            self.assertEqual(result, {n: (len(b), 'sha256:'+d.sha(b)) for n,b in files.items()})
            with self.assertRaisesRegex(ValueError, 'verified lecture manifest'):
                release.unpack_checked(raw, dict(cfg, index_sha256='f'*64), Path(tmp))

    def test_existing_complete_published_release_only_receives_read_requests(self):
        head = 'a'*40
        cfg = dict(producer_sha=head, index_sha256='a'*64, descriptor_sha256='b'*64)
        expected = {'index.json': (10, 'sha256:'+'d'*64)}
        current = dict(tag_name=release.TAG, target_commitish=head, id=3, draft=False)
        assets = [dict(name='index.json', size=10, digest='sha256:'+'d'*64, state='uploaded')]
        ref = dict(ref='refs/tags/'+release.TAG, object=dict(type='commit', sha=head, url='ref'))
        def gh(*args, **kwargs):
            self.assertEqual(args[:2], ('api', release.BASE+'/releases?per_page=100'))
            return [[current]]
        def api(path):
            if path.startswith('git/matching-refs/'): return [ref]
            if path.startswith('git/ref/'): return ref
            if path.startswith('releases/tags/'): return current
            self.assertEqual(path, 'releases/3/assets?per_page=100')
            return assets
        with patch.object(release.d, 'gh', side_effect=gh), patch.object(release, 'api', side_effect=api):
            release.publish(cfg, Path('.'), expected)


if __name__ == '__main__':
    unittest.main()
