"""Independent source, audio, rejection and existing-v2-crypto contract checks.

All waveforms and keys here are synthetic. These tests do not claim production
speech naturalness, human listening approval, model inference or publication.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
import wave

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'research' / 'reading-quality'))
from paragraph_plan import build_plan
from assemble import (AssemblyError, INPUT_RATE, SAMPLE_RATE, assemble_lesson, identity,
                      _compact_long_pauses, _metrics, validate_entries, validate_plan)
from lecture_pack import make_pack_manifest, open_part, seal_part, validate_pack


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make_wave(path, *, rate=INPUT_RATE, count=10080, frequency=220, channels=1,
              clipped=False, silent=False, pause_seconds=0):
    samples = .17 * np.sin(2 * np.pi * frequency * np.arange(count) / rate)
    if silent:
        samples[:] = 0
    if clipped:
        samples[100:200] = 1
    if pause_seconds:
        middle = len(samples) // 2
        samples = np.concatenate((samples[:middle], np.zeros(rate * pause_seconds), samples[middle:]))
    samples = np.clip(samples * 32767, -32768, 32767).astype('<i2')
    if channels > 1:
        samples = np.repeat(samples[:, None], channels, axis=1)
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(samples.tobytes())


class AssemblyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic-bora-assembly-')
        self.root = Path(self.temp.name)
        source = ('\n  😀 첫 번째 문장을 또렷하게 읽습니다. 두 번째 문장을 확인하고 다음 설명을 이어갑니다.\n\n'
                  '마지막 문단도 빠짐없이 읽습니다.\n')
        self.plan = build_plan(source, 'TB-001', max_chars=40)
        self.settings = dict(schema='bora-python-fp32-production/v1', engine='qwen-tts-python',
                             dtype='float32', threads=4, attention='sdpa', device='cpu',
                             inference_mode=True, sample_rate=INPUT_RATE, tempo_factor=1, pitch_shift=0,
                             unit_gap_s=.18, paragraph_gap_s=.35)
        self.settings_hash = identity(self.settings)
        self.recipe_hash = identity(dict(name='synthetic-test-only', settings=self.settings_hash))
        self.entries = []
        for i, unit in enumerate(self.plan['units']):
            folder = self.root / f'unit-{i:04d}'
            folder.mkdir()
            path = folder / 'final.wav'
            make_wave(path, frequency=220 + i * 40)
            receipt = dict(schema='bora-fp32-unit-receipt/v1', status='passed', blocked=False,
                           production_eligible=True, automatic_checks_passed=True,
                           review_required=False, review_flags=[], natural_eos=True,
                           perceptual_review_completed=False, file='final.wav', sha256=sha(path),
                           sample_rate=INPUT_RATE, sample_count=10080, measurement_kind='final_decoded_pcm',
                           lesson_id=self.plan['lesson_id'], unit_index=i, unit_id=unit['id'],
                           paragraph_id=unit['paragraph_id'], mode=unit['mode'],
                           start_utf16=unit['start_utf16'], end_utf16=unit['end_utf16'],
                           source_sha256=unit['source_sha256'],
                           spoken_sha256=hashlib.sha256(unit['spoken'].encode()).hexdigest(),
                           plan_sha256=identity(self.plan), recipe_sha256=self.recipe_hash,
                           render_settings_sha256=self.settings_hash, render_settings=copy.deepcopy(self.settings),
                           unit_key=identity(dict(unit=unit, render_settings_sha256=self.settings_hash)),
                           asr=dict(blocked=False, review_required=False, character_errors=0, missing_critical=[]))
            self.entries.append(dict(unit_index=i, path=path, receipt=receipt))
        self.args = dict(expected_recipe_sha256=self.recipe_hash,
                         expected_render_settings_sha256=self.settings_hash,
                         unit_gap_ms=180, paragraph_gap_ms=350)

    def tearDown(self):
        self.temp.cleanup()

    def validate(self, entries=None, plan=None):
        return validate_entries(plan or self.plan, self.entries if entries is None else entries,
                                self.recipe_hash, self.settings_hash)

    def test_real_encode_decode_offsets_gaps_and_v2_encryption_roundtrip(self):
        out = self.root / 'assembled'
        metadata = assemble_lesson(self.plan, self.entries[::-1], out, max_part_seconds=1,
                                   title='합성 테스트', **self.args)
        self.assertGreater(len(metadata['audio_parts']), 1)
        source, cursor, sample_cursor = self.plan['source'], 0, 0
        for i, segment in enumerate(metadata['segments']):
            self.assertEqual(segment['start_cp'], cursor)
            self.assertEqual(source[segment['start_cp']:segment['end_cp']], segment['text'])
            self.assertEqual(len(source[:segment['end_cp']].encode('utf-16-le')) // 2, segment['end_utf16'])
            self.assertEqual(segment['end_sample'] - segment['start_sample'], round(10080 * 44100 / 24000))
            gap_ms = 0 if i == 0 else (350 if segment['paragraph_id'] != metadata['segments'][i-1]['paragraph_id'] else 180)
            self.assertEqual(segment['start_sample'] - sample_cursor, round(gap_ms * 44100 / 1000))
            cursor, sample_cursor = segment['end_cp'], segment['end_sample']
        self.assertEqual(cursor, len(source))
        self.assertEqual(sample_cursor, metadata['total_samples'])
        self.assertEqual(metadata['validation']['duration_s'], sample_cursor / SAMPLE_RATE)
        self.assertTrue(metadata['validation']['finite'])
        self.assertEqual(metadata['validation']['clipped_samples'], 0)
        self.assertFalse(metadata['human_listening_verified'])
        self.assertNotIn('audio_pack_sha256', metadata, 'protected wrapper must set the actual ciphertext pack hash')
        self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o700)
        self.assertFalse((out / '.pcm.f32le').exists())
        self.assertEqual(json.loads((out / 'TB-001.metadata.json').read_text()), metadata)
        key, pack_id = bytes(range(32)), '11' * 16
        manifests, blobs, offset = [], [], 0
        for part in metadata['audio_parts']:
            file = out / part['file_name']
            self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)
            self.assertEqual(sha(file), part['audio_sha256'])
            self.assertLessEqual(abs(part['decoded_sample_delta']), 1)
            self.assertLessEqual(part['end_sample'] - part['start_sample'], SAMPLE_RATE)
            plain = file.read_bytes()
            manifest, encrypted = seal_part(key, plain, 'TB-001', pack_id, part['index'],
                                            part['start_sample'], part['end_sample'], offset)
            self.assertEqual(open_part(key, manifest, encrypted, expected_id='TB-001',
                                       expected_part_index=part['index'], expected_pack_id=pack_id), plain)
            manifests.append(manifest)
            blobs.append(encrypted)
            offset += len(encrypted)
        pack = make_pack_manifest('TB-001', pack_id, manifests, b''.join(blobs))
        validate_pack(pack, b''.join(blobs))
        self.assertEqual(pack['total_samples'], metadata['total_samples'])

    def single_pass_entries(self):
        settings = dict(self.settings, execution_profile='single-pass-no-asr',
                        transcript_gate='not_run_single_pass_no_asr/v1', seeds=[5192],
                        asr=dict(status='not_run', performed=False))
        settings_hash = identity(settings)
        entries = copy.deepcopy(self.entries)
        for unit, row in zip(self.plan['units'], entries):
            row['receipt'].update(render_settings=settings, render_settings_sha256=settings_hash,
                unit_key=identity(dict(unit=unit, render_settings_sha256=settings_hash)),
                asr=dict(status='not_run', performed=False), seed=5192,
                candidates=[dict(seed=5192, natural_eos=True, cap_reached=False, eos_seen=True,
                    generated_sequence_tokens=300, eos_token_id=151645, last_token=151645,
                    asr=dict(status='not_run', performed=False), signal=dict(blocked=False))])
        return entries, settings_hash

    def test_single_pass_requires_explicit_profile_and_truthfully_labels_output(self):
        entries, settings_hash = self.single_pass_entries()
        with self.assertRaisesRegex(AssemblyError, 'receipt_transcript_gate'):
            validate_entries(self.plan, entries, self.recipe_hash, settings_hash)
        with self.assertRaisesRegex(AssemblyError, 'receipt_single_pass_asr_marker'):
            validate_entries(self.plan, self.entries, self.recipe_hash, self.settings_hash,
                             profile='single_pass')
        metadata = assemble_lesson(self.plan, entries, self.root / 'single-pass',
                                   **dict(self.args, expected_render_settings_sha256=settings_hash),
                                   profile='single_pass')
        self.assertFalse(metadata['asr_verified'])
        self.assertTrue(metadata['asr_scope'].startswith('not run;'))
        self.assertTrue(metadata['automatic_unit_checks_passed'])

    def test_single_pass_compacts_only_long_quiet_pauses_and_remeasures_timing(self):
        entries, settings_hash = self.single_pass_entries()
        sample = entries[0]
        make_wave(sample['path'], pause_seconds=4)
        sample['receipt'].update(sha256=sha(sample['path']), sample_count=10080 + 4 * INPUT_RATE)
        metadata = assemble_lesson(self.plan, entries, self.root / 'compacted',
                                   **dict(self.args, expected_render_settings_sha256=settings_hash),
                                   profile='single_pass')
        first, second = metadata['segments'][:2]
        self.assertLess(first['end_s'], 1)
        self.assertAlmostEqual(second['start_s'] - first['end_s'], .18)
        self.assertLess(metadata['validation']['max_silence_s'], 3)

    def test_pause_compaction_preserves_active_and_borderline_frames(self):
        active = np.full(SAMPLE_RATE, .1, dtype=np.float32)
        pause = np.zeros(4 * SAMPLE_RATE, dtype=np.float32)
        borderline = np.full(SAMPLE_RATE, .0018, dtype=np.float32)
        pcm = np.concatenate((active, pause, borderline, active))
        shortened = _compact_long_pauses(pcm)
        np.testing.assert_array_equal(shortened[:SAMPLE_RATE], active)
        np.testing.assert_array_equal(shortened[-2 * SAMPLE_RATE:-SAMPLE_RATE], borderline)
        np.testing.assert_array_equal(shortened[-SAMPLE_RATE:], active)
        self.assertLess(_metrics(shortened)['max_silence_s'], 3)
        self.assertIs(_compact_long_pauses(pause), pause)

    def test_single_pass_rejects_second_attempt_forged_asr_or_invalid_eos(self):
        for field, value, code in (
                ('candidates', None, 'receipt_single_pass_attempt'),
                ('asr', dict(status='not_run', performed=0), 'receipt_single_pass_asr_marker'),
                ('natural_eos', False, 'receipt_missing_natural_eos'),
                ('cap_reached', True, 'candidate_single_pass_eos')):
            entries, settings_hash = self.single_pass_entries()
            receipt = entries[0]['receipt']
            target = receipt['candidates'][0] if field == 'cap_reached' else receipt
            if field == 'candidates':
                value = receipt['candidates'] * 2
            target[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(AssemblyError, code):
                validate_entries(self.plan, entries, self.recipe_hash, settings_hash,
                                 profile='single_pass')

    def test_missing_duplicate_out_of_range_or_bool_index_rejected(self):
        bad = [self.entries[:-1], [self.entries[0]] * len(self.entries)]
        for index in (-1, len(self.entries), True):
            rows = copy.deepcopy(self.entries)
            rows[0]['unit_index'] = index
            bad.append(rows)
        for rows in bad:
            with self.subTest(rows=rows[0]['unit_index'] if rows else None), self.assertRaises(AssemblyError):
                self.validate(rows)

    def test_stale_identity_hash_mode_and_bounds_rejected(self):
        mutations = {'recipe_sha256': 'a' * 64, 'plan_sha256': 'a' * 64,
                     'source_sha256': 'a' * 64, 'spoken_sha256': 'a' * 64,
                     'render_settings_sha256': 'a' * 64, 'unit_key': 'a' * 64,
                     'unit_id': 'TB-002:p0000:u000', 'paragraph_id': 'TB-001:p0001',
                     'lesson_id': 'TB-002', 'start_utf16': 1, 'end_utf16': 1,
                     'mode': 'reading', 'file': 'review.wav', 'unit_index': True,
                     'measurement_kind': 'estimated', 'sample_rate': 44100}
        for key, value in mutations.items():
            rows = copy.deepcopy(self.entries)
            rows[0]['receipt'][key] = value
            with self.subTest(field=key), self.assertRaises(AssemblyError):
                self.validate(rows)

    def test_review_only_cap_or_transcript_mismatch_never_passes(self):
        mutations = [('status', 'blocked_requires_review'), ('blocked', True),
                     ('production_eligible', False), ('automatic_checks_passed', False),
                     ('review_required', True), ('review_flags', ['reading_mode_unverified']),
                     ('review_only', True), ('review_file', 'review.wav'), ('natural_eos', False)]
        for key, value in mutations:
            rows = copy.deepcopy(self.entries)
            rows[0]['receipt'][key] = value
            with self.subTest(field=key), self.assertRaises(AssemblyError):
                self.validate(rows)
        for key, value in [('blocked', True), ('review_required', True),
                           ('character_errors', 1), ('missing_critical', ['의무'])]:
            rows = copy.deepcopy(self.entries)
            rows[0]['receipt']['asr'][key] = value
            with self.subTest(asr=key), self.assertRaisesRegex(AssemblyError, 'receipt_transcript_gate'):
                self.validate(rows)

    def test_wrong_profile_even_with_self_consistent_settings_hash_is_rejected(self):
        for key, value in [('dtype', 'bfloat16'), ('threads', 2), ('engine', 'onnx'), ('tempo_factor', 1.1)]:
            rows = copy.deepcopy(self.entries)
            settings = copy.deepcopy(self.settings)
            settings[key] = value
            changed_hash = identity(settings)
            for row, unit in zip(rows, self.plan['units']):
                row['receipt'].update(render_settings=settings, render_settings_sha256=changed_hash,
                                      unit_key=identity(dict(unit=unit, render_settings_sha256=changed_hash)))
            with self.subTest(profile=key), self.assertRaisesRegex(AssemblyError, 'not_selected_fp32_profile'):
                validate_entries(self.plan, rows, self.recipe_hash, changed_hash)

    def test_plan_gaps_surrogate_boundary_and_unresolved_review_rejected(self):
        for mutate in (lambda p: p['units'][0].update(end_utf16=4),
                       lambda p: p['units'][1].update(start_utf16=p['units'][1]['start_utf16'] + 1),
                       lambda p: p['units'][0].update(review=['reading_mode_unverified']),
                       lambda p: p['units'][0].update(reading_mode_verified=False),
                       lambda p: p.update(source_sha256='0' * 64),
                       lambda p: p.update(merge_across_paragraphs=True)):
            plan = copy.deepcopy(self.plan)
            mutate(plan)
            with self.assertRaises(AssemblyError):
                validate_plan(plan)

    def test_input_bytes_rechecked_before_resample(self):
        self.entries[0]['path'].write_bytes(self.entries[0]['path'].read_bytes() + b'tampered')
        out = self.root / 'bad'
        with self.assertRaisesRegex(AssemblyError, 'unit_audio_hash'):
            assemble_lesson(self.plan, self.entries, out, **self.args)
        self.assertFalse(out.exists())
        self.assertEqual(list(self.root.glob('.bora-assembly-*')), [])

    def test_actual_wav_rate_channels_count_silence_and_clipping(self):
        for params, reason in [({'rate': 22050}, 'unit_wav_format'),
                               ({'channels': 2}, 'unit_wav_format'),
                               ({'count': 11000}, 'unit_decoded_samples'),
                               ({'silent': True}, 'unit_silent_pcm'),
                               ({'clipped': True}, 'unit_clipped_pcm')]:
            rows = copy.deepcopy(self.entries)
            make_wave(rows[0]['path'], **params)
            rows[0]['receipt']['sha256'] = sha(rows[0]['path'])
            out = self.root / 'bad'
            with self.subTest(params=params), self.assertRaisesRegex(AssemblyError, reason):
                assemble_lesson(self.plan, rows, out, **self.args)
            self.assertFalse(out.exists())
        make_wave(self.entries[0]['path'])

    def test_existing_output_and_symlinks_are_not_overwritten(self):
        out = self.root / 'existing'
        out.mkdir()
        keep = out / 'keep.txt'
        keep.write_text('preserve')
        with self.assertRaisesRegex(AssemblyError, 'output_directory_not_fresh'):
            assemble_lesson(self.plan, self.entries, out, **self.args)
        self.assertEqual(keep.read_text(), 'preserve')
        link = self.root / 'linked.wav'
        link.symlink_to(self.entries[0]['path'])
        rows = copy.deepcopy(self.entries)
        rows[0]['path'] = link
        with self.assertRaisesRegex(AssemblyError, 'symlink_path'):
            self.validate(rows)

    def test_pinned_gap_cannot_silently_change(self):
        args = dict(self.args, paragraph_gap_ms=0)
        with self.assertRaisesRegex(AssemblyError, 'pinned_gap_setting'):
            assemble_lesson(self.plan, self.entries, self.root / 'bad', **args)

    def test_mp3_decode_mismatch_is_a_blocking_gate(self):
        # Actual encoder/decoder behavior is tested above; this negative control
        # proves the assembler does not publish when that independent check fails.
        import assemble
        from unittest.mock import patch
        original = assemble._run

        def corrupt_decode(args, data=None):
            result = original(args, data)
            if 'pipe:1' in args and any(str(x).endswith('.mp3') for x in args):
                return result[:-400]
            return result

        out = self.root / 'bad'
        with patch.object(assemble, '_run', side_effect=corrupt_decode):
            with self.assertRaisesRegex(AssemblyError, 'part_decoded_sample_mismatch'):
                assemble_lesson(self.plan, self.entries, out, **self.args)
        self.assertFalse(out.exists())


if __name__ == '__main__':
    unittest.main()
