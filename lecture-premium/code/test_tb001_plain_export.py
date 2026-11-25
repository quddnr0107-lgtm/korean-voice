import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch
import zipfile


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.owner = types.SimpleNamespace(RELEASES={'TB': ('bora-fp32-tb-single-pass-v1-20261114',
            '174fce697dc12f9aa3d6da7d6e98ddd8e9c1a3c79ac7e6ba39ef1b9de2dd1290', 25)}, download=Mock())
        with patch.dict(sys.modules, {'owner_video_export': self.owner}):
            spec = importlib.util.spec_from_file_location('scoped', Path(__file__).with_name('tb001_plain_export.py'))
            self.mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.mod)
        self.data = b'synthetic-test-only'
        self.audio = dict(lesson_id='TB-001', sample_rate=44100, total_samples=44100,
                          pack_sha256='fixture', parts=[dict(part_index=0, start_sample=0,
                            end_sample=44100, plain_bytes=len(self.data))])
        self.record = dict(id='TB-001', sample_rate=44100, total_samples=44100,
                           audio_pack_sha256='fixture', narration_text='테스트 문장',
                           segments=[dict(start_sample=0, end_sample=44100, text='테스트 문장',
                                          private_field='excluded')], private_field='excluded')

    def fixture(self, extra=False):
        b = io.BytesIO()
        with zipfile.ZipFile(b, 'w') as z:
            z.writestr('metadata.json', json.dumps(self.record))
            z.writestr('descriptor.json', json.dumps({'audio': self.audio}))
            z.writestr('part-000.mp3', self.data)
            if extra:
                z.writestr('other.txt', 'excluded')
        return b.getvalue()

    def test_exact_bytes_minimal_metadata(self):
        with zipfile.ZipFile(io.BytesIO(self.mod.plain_package(self.fixture()))) as z:
            self.assertEqual(set(z.namelist()), {'part-000.mp3', 'timeline.json'})
            self.assertEqual(z.read('part-000.mp3'), self.data)
            t = json.loads(z.read('timeline.json'))
            self.assertEqual(t['parts'][0]['sha256'], hashlib.sha256(self.data).hexdigest())
            self.assertEqual(set(t['segments'][0]), {'start_sample', 'end_sample', 'text'})
            self.assertNotIn('private_field', t)

    def test_wrong_lesson(self):
        self.record['id'] = 'TB-002'
        with self.assertRaises(ValueError): self.mod.plain_package(self.fixture())

    def test_wrong_clock(self):
        self.record['total_samples'] -= 1
        with self.assertRaises(ValueError): self.mod.plain_package(self.fixture())

    def test_wrong_part_length(self):
        self.audio['parts'][0]['plain_bytes'] += 1
        with self.assertRaises(ValueError): self.mod.plain_package(self.fixture())

    def test_unexpected_member(self):
        with self.assertRaises(ValueError): self.mod.plain_package(self.fixture(extra=True))

    def test_incomplete_text(self):
        self.record['narration_text'] += ' missing'
        with self.assertRaises(ValueError): self.mod.plain_package(self.fixture())

    def test_download_exact_scope(self):
        self.mod.main(['download', 'inputs'])
        self.owner.download.assert_called_once_with(Path('inputs'), 'TB', ['TB-001'])

    def test_release_pin(self):
        self.owner.RELEASES['TB'] = ('wrong', 'wrong', 25)
        with self.assertRaises(ValueError): self.mod.main(['download', 'inputs'])
        self.owner.download.assert_not_called()


if __name__ == '__main__':
    unittest.main()
