"""Public MP3 handoff tests use synthetic waveforms, never private inputs."""
import copy
import hashlib
import tempfile
from pathlib import Path
import unittest

import numpy as np
from assemble import _encode_part, SAMPLE_RATE
import ms018_public_audio as audio
import ms018_recovery as recovery


class PublicAudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root/'assembled'
        self.source.mkdir()
        self.parts = []
        cursor = 0
        for i, freq in enumerate((220, 330)):
            count = SAMPLE_RATE
            pcm = (.15*np.sin(2*np.pi*freq*np.arange(count)/SAMPLE_RATE)).astype('<f4').tobytes()
            name = f'MS-018.part-{i:04d}.mp3'
            info = _encode_part(pcm, self.source/name, count, 'ffmpeg')
            self.parts.append(dict(index=i, file_name=name, start_sample=cursor,
                                  end_sample=cursor+count, **info))
            cursor += count
        self.metadata = dict(id='MS-018', plan_sha256=recovery.PLAN,
            recipe_sha256=recovery.RECIPE, render_settings_sha256=recovery.SETTINGS,
            automatic_unit_checks_passed=True, sample_rate=SAMPLE_RATE, total_samples=cursor,
            ms018_seed_recovery=dict(unit_index=50), audio_parts=self.parts,
            narration_text='private fixture excluded', segments=[{'text':'private fixture excluded'}])

    def test_exact_audio_only_and_continuous_mp3(self):
        parts = self.root/'public'
        manifest = audio.export_parts(self.source, self.metadata, parts)
        self.assertEqual(set(p.name for p in parts.iterdir()),
                         {'audio-manifest.json','MS-018.part-0000.mp3','MS-018.part-0001.mp3'})
        self.assertNotIn('private fixture', (parts/'audio-manifest.json').read_text())
        for part in manifest['parts']:
            self.assertEqual((parts/part['file']).read_bytes(), (self.source/part['file']).read_bytes())
        out = self.root/audio.MP3
        result = audio.join_audio(parts, out)
        self.assertEqual(result['samples'], 2*SAMPLE_RATE)
        self.assertEqual(result['original_sample_delta'], 0)
        self.assertEqual(result['sha256'], hashlib.sha256(out.read_bytes()).hexdigest())
        self.assertTrue(out.stat().st_size > 0)

    def test_reject_wrong_lesson_or_changed_part(self):
        wrong = dict(self.metadata, id='MS-022')
        with self.assertRaises(ValueError):
            audio.export_parts(self.source, wrong, self.root/'public')
        path = self.source/'MS-018.part-0000.mp3'
        raw = path.read_bytes()
        path.write_bytes(bytes([raw[0]^1])+raw[1:])
        with self.assertRaises(ValueError):
            audio.export_parts(self.source, self.metadata, self.root/'public')
        self.assertFalse((self.root/'public').exists())

    def test_reject_extra_files_in_public_parts(self):
        out = self.root/'public'
        audio.export_parts(self.source, self.metadata, out)
        (out/'private.json').write_text('not allowed')
        with self.assertRaises(ValueError):
            audio.join_audio(out, self.root/audio.MP3)
        self.assertFalse((self.root/audio.MP3).exists())


if __name__ == '__main__':
    unittest.main()
