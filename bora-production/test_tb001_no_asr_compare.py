"""Synthetic regression fixtures for the protected one-attempt comparison."""

import contextlib
import copy
import hashlib
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import tb001_no_asr_compare as compare


def waveform(samples):
    fmt = struct.pack('<HHIIHH', 3, 1, 24000, 96000, 4, 32)
    pcm = b''.join(struct.pack('<f', sample) for sample in samples)
    chunks = b'fmt ' + struct.pack('<I', len(fmt)) + fmt + b'data' + struct.pack('<I', len(pcm)) + pcm
    return b'RIFF' + struct.pack('<I', len(chunks) + 4) + b'WAVE' + chunks


class ComparisonTests(unittest.TestCase):
    def test_group_selection_keeps_exact_target_coverage(self):
        indices = [index for group in range(1, 6) for index in compare.group_indices(group)]
        self.assertEqual(sorted(indices), list(compare.TARGETS))
        for invalid in (0, 6, True, '1'):
            with self.assertRaises(ValueError):
                compare.group_indices(invalid)

    def test_protected_context_rejects_other_workflow_or_root(self):
        env = dict(GITHUB_REPOSITORY=compare.full.d.REPO,
                   GITHUB_REF='refs/heads/' + compare.full.d.BRANCH,
                   GITHUB_EVENT_NAME='push', GITHUB_WORKFLOW_REF=compare.WORKFLOW)
        compare.check_context(env, 1001, 1001)
        with self.assertRaises(ValueError):
            compare.check_context(env, 0, 0)
        with self.assertRaises(ValueError):
            compare.check_context(dict(env, GITHUB_WORKFLOW_REF='other'), 1001, 1001)

    def original(self):
        first = compare.preview.wav_info(waveform([.25] * 25))
        second = compare.preview.wav_info(waveform([.125] * 30))
        settings = dict(model=compare.fp32.MODEL_REPOSITORY,
            model_revision=compare.fp32.MODEL_REVISION, speaker='Sohee', language='Korean',
            device='cpu', dtype='float32', attention='sdpa', threads=4, sample_rate=24000,
            seeds=list(compare.fp32.SEEDS), styles=compare.fp32.STYLES,
            sampling=dict(compare.fp32.SAMPLING, max_new_tokens=1100),
            model_sha256={name: 'a' * 64 for name in compare.fp32.MODEL_REQUIRED})
        candidates = []
        for seed, info, seconds in zip(compare.fp32.SEEDS, (first, second), (10.125, 23.5)):
            candidates.append(dict(seed=seed, tts_seconds=seconds, sha256=info['original_wav_sha256'],
                natural_eos=True, cap_reached=False, signal=dict(samples=info['sample_count'],
                    sample_rate=info['sample_rate'], seconds=info['seconds'], blocked=False),
                asr=dict(blocked=True, review_required=True, character_errors=1, missing_critical=[])))
        receipt = dict(status='blocked_requires_review', blocked=True, review_only=True,
            production_eligible=False, review_flags=[], render_settings=settings,
            review_sha256=second['original_wav_sha256'], candidates=candidates)
        return receipt, first, second

    def project(self, receipt, review):
        settings_pin = compare.preview.digest(compare.preview.canonical(receipt['render_settings']))
        with patch.dict(compare.full.PIN, render_settings_sha256=settings_pin):
            return compare.baseline_from_receipt(receipt, review, review['original_wav_sha256'])

    def test_original_retry_time_and_both_reference_lengths(self):
        receipt, first, second = self.original()
        baseline = self.project(receipt, second)
        self.assertEqual(baseline['old_attempts'], 2)
        self.assertEqual(baseline['old_tts_seconds'], 33.625)
        self.assertEqual(baseline['old_first_tts_seconds'], 10.125)
        self.assertEqual(baseline['old_retry_tts_seconds'], 23.5)
        self.assertEqual(baseline['old_first_audio_seconds'], first['seconds'])
        self.assertEqual(baseline['old_strict_reference_audio_seconds'], second['seconds'])
        self.assertEqual(baseline['old_first_wav_sha256'], first['original_wav_sha256'])

    def test_history_rejects_other_seed_or_non_asr_retry(self):
        receipt, _, second = self.original()
        for field, value in [('seed', 7319), ('tts_seconds', float('nan')),
                             ('natural_eos', False), ('asr', {'blocked': False}),
                             ('signal', {'blocked': True})]:
            altered = copy.deepcopy(receipt)
            altered['candidates'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.project(altered, second)

    def test_one_attempt_uses_same_unit_voice_and_persists_numeric_receipt(self):
        receipt, _, second = self.original()
        baseline = self.project(receipt, second)
        index = compare.TARGETS[0]
        source = 'Synthetic test only.'
        unit = dict(id='TB-001:p0000:u000', paragraph_id='TB-001:p0000', source=source,
            spoken='합성 측정용 문장입니다.', mode='reading', start_utf16=0,
            end_utf16=len(source), source_sha256=hashlib.sha256(source.encode()).hexdigest(),
            reading_mode_verified=True, critical_terms=[])
        calls, seeds = [], []
        eos_tokens = SimpleNamespace(tolist=lambda: [4, 5, 99])
        original = lambda: SimpleNamespace(sequences=[eos_tokens])
        talker = SimpleNamespace(generate=original)

        def generate_custom_voice(**kwargs):
            calls.append(kwargs)
            talker.generate()
            return [[.25] * 25], 24000

        model = SimpleNamespace(generate_custom_voice=generate_custom_voice,
            model=SimpleNamespace(talker=talker,
                config=SimpleNamespace(talker_config=SimpleNamespace(codec_eos_token_id=99))))
        torch = SimpleNamespace(manual_seed=seeds.append, inference_mode=contextlib.nullcontext)
        soundfile = SimpleNamespace(write=lambda filename, wave, rate, subtype:
                                   Path(filename).write_bytes(waveform(wave)))
        with tempfile.TemporaryDirectory() as td, patch.object(compare.fp32, 'require_guard'), \
             patch.object(compare.fp32, 'signal_stats', return_value={'blocked': False, 'samples': 25}):
            wav, row = compare.render_unit(model, torch, soundfile, index, unit, baseline, td)
            self.assertEqual(wav.read_bytes(), waveform([.25] * 25))
            with self.assertRaises(ValueError):
                compare.render_unit(model, torch, soundfile, index, unit, baseline, td)
        self.assertEqual(seeds, [5192])
        self.assertIs(talker.generate, original)
        self.assertEqual(calls, [dict(text=unit['spoken'], language='Korean', speaker='Sohee',
            instruct=compare.fp32.STYLES['reading'], **compare.fp32.SAMPLING, max_new_tokens=1100)])
        self.assertEqual(row['new_attempts'], 1)
        self.assertEqual(row['audio_sample_count'], 25)
        self.assertTrue(row['matches_old_first_wav'])
        self.assertFalse(row['matches_old_saved_review_wav'])
        self.assertFalse(row['asr_performed'])
        self.assertFalse(row['production_eligible'])
        self.assertGreaterEqual(row['new_unit_wall_seconds'], row['new_tts_seconds'])
        self.assertNotIn('old_model_assets_sha256', row)
        self.assertNotIn(unit['spoken'], repr(row))


if __name__ == '__main__':
    unittest.main()
