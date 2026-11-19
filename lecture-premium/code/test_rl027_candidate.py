import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np

import lecture_crypto as crypto
import rl027_candidate as c


class CandidateTests(unittest.TestCase):
    def test_checked_in_request_cannot_widen_scope(self):
        p = Path(__file__).resolve().parents[1] / 'rl027-unit44-request.json'
        c.check_request(p)
        with tempfile.TemporaryDirectory() as tmp:
            other = Path(tmp) / 'unexpected.json'
            edited = json.loads(p.read_text())
            edited['unit'] = 43
            other.write_text(json.dumps(edited))
            with self.assertRaises(ValueError):
                c.check_request(other)

    def test_pcm_envelope_roundtrip_without_public_plaintext_and_roundtrip_rejects_tampering(self):
        key = bytes(range(32))
        pcm = bytes(range(120))
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'candidate'
            c.seal_candidate(folder, key, pcm, {'schema': c.SCHEMA})
            header = json.loads((folder / 'RL-027.unit44.candidate.sealed.json').read_bytes())
            cipher = (folder / 'RL-027.unit44.candidate.sealed.bin').read_bytes()
            self.assertNotIn(pcm, cipher)
            plain, _ = crypto.open_range(key, header, cipher, expected_id=c.LESSON,
                                         expected_kind='checkpoint', expected_key_id=c.delivery.KEY_ID)
            with zipfile.ZipFile(io.BytesIO(plain)) as archive:
                self.assertEqual(archive.namelist(), ['candidate.json', 'unit.s16le'])
                self.assertEqual(archive.read('unit.s16le'), pcm)
                self.assertEqual(json.loads(archive.read('candidate.json'))['schema'], c.SCHEMA)
            with self.assertRaises(Exception):
                crypto.open_range(key, header, bytes([cipher[0] ^ 1]) + cipher[1:],
                                  expected_id=c.LESSON, expected_kind='checkpoint', expected_key_id=c.delivery.KEY_ID)

    def test_unchanged_timing_or_natural_delta_without_long_padding(self):
        rng = np.random.default_rng(44)
        y = (rng.normal(0, 1800, 4 * c.RATE)).astype('<i2')
        y[-c.RATE // 2:] = 0
        pcm = y.tobytes()
        self.assertEqual(c.fit_candidate(pcm, len(y)), (pcm, 'unchanged'))
        self.assertEqual(c.fit_candidate(pcm, len(y)*2), (pcm, 'natural_duration'))
        fitted, mode = c.fit_candidate(pcm, round(len(y)*1.02))
        self.assertIn(mode, ('matched_duration', 'natural_duration'))
        if mode == 'matched_duration':
            self.assertEqual(len(fitted)//2, round(len(y)*1.02))

    def test_tone_or_omitted_words_always_fail_at_normal_and_accelerated_speeds(self):
        class Word:
            text = '제삼항 제십이조제이항을 위반하여 정치운동에 관여한 사람은 삼 년 이하의 징역 또는 삼천만원 이하의 벌금에 처한다.'
        class Recognizer:
            def transcribe(self, signal, **kwargs):
                return [Word()], None
        t = np.arange(c.RATE*3)/c.RATE
        tone = (3500*np.sin(2*np.pi*470*t)).astype('<i2').tobytes()
        accepted, checks = c.score_candidate(tone, (Recognizer(), {}))
        self.assertFalse(accepted)
        self.assertTrue(all(item['words']['accepted'] for item in checks))
        self.assertTrue(all(item['acoustic']['tone_runs'] for item in checks))
        Word.text = '정치운동에 관여한 사람은 벌금에 처한다.'
        noise = np.random.default_rng(10).normal(0, 1700, c.RATE*3).astype('<i2').tobytes()
        accepted, checks = c.score_candidate(noise, (Recognizer(), {}))
        self.assertFalse(accepted)
        self.assertTrue(all(not item['words']['accepted'] for item in checks))

    def test_models_run_with_whitelisted_offline_inputs_only(self):
        # No private field may be sent to synthesis from an unapproved unit.
        with tempfile.TemporaryDirectory() as tmp, patch.object(c, 'guarded'):
            p = Path(tmp)/'unit.json'
            p.write_text('{}')
            with patch.dict(c.os.environ, {'ADMIN_LAW_PREMIUM_MEDIA_KEY_B64':'test'}):
                with self.assertRaisesRegex(ValueError, 'keys_in_synth'):
                    c.synthesize(p, Path(tmp)/'generated', None, None)


if __name__ == '__main__':
    unittest.main()
