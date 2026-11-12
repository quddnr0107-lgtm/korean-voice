"""Regression tests for production gates; no speech model or network is used.

Synthetic PCM and fake inference objects test rejection, recipe fidelity,
checkpoint identity and reusable-engine control flow. Actual TTS/ASR quality is
verified separately by the production keyless preflight.
"""
import contextlib
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
import wave

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fp32_renderer as render


def unit(text='병역증 또는 전역증을 지니고 다녀야 한다.', ident='TB-001:p0000:u000'):
    return dict(id=ident, paragraph_id=ident.rsplit(':u', 1)[0], source=text, spoken=text,
                source_sha256=hashlib.sha256(text.encode()).hexdigest(),
                start_utf16=0, end_utf16=len(text.encode('utf-16-le')) // 2,
                mode='explain', reading_mode_verified=True, review=[], critical_terms=['병역증', '전역증'])


class SyntheticWavIO:
    """Real PCM16 WAV I/O for gate tests; production uses pinned libsndfile."""
    @staticmethod
    def write(path, data, rate, subtype=None):
        a = np.asarray(data)
        with wave.open(str(path), 'wb') as out:
            out.setnchannels(1); out.setsampwidth(2); out.setframerate(rate)
            out.writeframes(np.round(a * 32767).astype('<i2').tobytes())

    @staticmethod
    def read(path, dtype=None):
        with wave.open(str(path), 'rb') as src:
            return np.frombuffer(src.readframes(src.getnframes()), dtype='<i2').astype('float32') / 32767, src.getframerate()


def synthetic_wave():
    return (0.15 * np.sin(np.arange(24000, dtype='float64') * 2 * np.pi * 400 / 24000)).astype('float32')


def bare_renderer():
    r = render.FP32Renderer.__new__(render.FP32Renderer)
    r.sf = SyntheticWavIO
    r.max_new_tokens = 1100
    r.seeds = (5192, 7319)
    r.settings = dict(schema=render.RECIPE_SCHEMA, engine='qwen-tts-python', dtype='float32',
                      threads=4, attention='sdpa', sampling=dict(render.SAMPLING, max_new_tokens=1100),
                      seeds=list(r.seeds), styles=copy.deepcopy(render.STYLES),
                      filters=copy.deepcopy(render.FILTERS))
    r.settings_sha256 = render.identity(r.settings)
    return r


def generated(directory, seed=5192, text=None, error_count=0, eos=True, clipped=False):
    name = f'candidate-{seed}.wav'
    SyntheticWavIO.write(directory / name, synthetic_wave(), 24000)
    return dict(seed=seed, file=name, sha256=render.file_hash(directory / name),
                natural_eos=eos, tts_seconds=10.0,
                signal=dict(blocked=clipped, samples=24000, sample_rate=24000),
                asr=dict(blocked=bool(error_count), review_required=bool(error_count),
                         character_errors=error_count, missing_critical=[], recognized=text or '합성 검사'))


def finalized(candidate, current_unit, directory):
    source = directory / candidate['file']
    (directory / 'final.wav').write_bytes(source.read_bytes())
    return dict(file='final.wav', sha256=render.file_hash(directory / 'final.wav'),
                sample_rate=24000, sample_count=24000,
                measurement_kind='final_decoded_pcm', trim={}, signal={'blocked': False})


class RecipeAndIdentityTests(unittest.TestCase):
    def test_exact_selected_sampling_is_not_native_engine_defaults(self):
        self.assertEqual(render.SAMPLING, dict(non_streaming_mode=True, do_sample=True,
            temperature=.8, top_p=.9, top_k=50, repetition_penalty=1.05,
            subtalker_dosample=True, subtalker_temperature=.9, subtalker_top_p=1.0,
            subtalker_top_k=50))
        r = bare_renderer()
        for field, value in [('dtype', 'bfloat16'), ('threads', 2), ('attention', 'eager'),
                             ('engine', 'native-int8')]:
            changed = copy.deepcopy(r.settings); changed[field] = value
            self.assertNotEqual(render.identity(changed), r.settings_sha256)
        changed = copy.deepcopy(r.settings)
        changed['sampling']['subtalker_dosample'] = False
        self.assertNotEqual(render.identity(changed), r.settings_sha256)

    def test_aliases_expose_identical_settings(self):
        r = bare_renderer()
        self.assertIs(r.render_settings, r.settings)
        self.assertEqual(r.render_settings_sha256, r.settings_sha256)

    def test_unit_source_and_spoken_both_bind_identity(self):
        u = unit(); r = bare_renderer()
        baseline = render.identity(dict(unit=u, render_settings_sha256=r.settings_sha256))
        changed = copy.deepcopy(u); changed['spoken'] += ' 다시.'
        self.assertNotEqual(baseline, render.identity(dict(unit=changed, render_settings_sha256=r.settings_sha256)))
        changed = copy.deepcopy(u); changed['mode'] = 'reading'
        self.assertNotEqual(baseline, render.identity(dict(unit=changed, render_settings_sha256=r.settings_sha256)))

    def test_utf16_and_optional_codepoint_offsets_are_preserved(self):
        u = unit('😀 병역증 또는 전역증입니다.')
        u.update(start_cp=17, end_cp=17 + len(u['source']))
        self.assertEqual(render.validate_unit(u), [])
        u['end_utf16'] -= 1
        with self.assertRaisesRegex(render.RenderFailure, 'offsets'):
            render.validate_unit(u)

    def test_invalid_source_identity_and_irrelevant_critical_terms_are_rejected(self):
        u = unit(); u['source'] += '변조'
        with self.assertRaises(render.RenderFailure): render.validate_unit(u)
        u = unit(); u['critical_terms'] = ['존재하지 않는 법']
        with self.assertRaisesRegex(render.RenderFailure, 'critical_terms'):
            render.validate_unit(u)

    def test_scope_rejects_nonexistent_lessons_and_mismatched_paragraph(self):
        for ident in ['TB-026:p0000:u000', 'MS-167:p0000:u000', 'RL-000:p0000:u000', 'XX-001:p0000:u000']:
            with self.subTest(ident=ident), self.assertRaises(render.RenderFailure):
                render.validate_unit(unit(ident=ident))
        u = unit(); u['paragraph_id'] = 'TB-001:p0001'
        with self.assertRaises(render.RenderFailure): render.validate_unit(u)

    def test_reading_and_preparation_review_flags_are_not_erased(self):
        u = unit(); u.update(review=['review_remaining_numeric_notation'], reading_mode_verified=False)
        self.assertEqual(render.validate_unit(u), ['reading_mode_unverified', 'review_remaining_numeric_notation'])

    def test_absent_reading_mode_review_is_not_implicitly_approved(self):
        u = unit(); u.pop('reading_mode_verified')
        self.assertEqual(render.validate_unit(u), ['reading_mode_unverified'])


class AssetPinTests(unittest.TestCase):
    def make_file(self, root, name, data, revision, etag=None):
        target = root / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
        meta = root / '.cache/huggingface/download' / (name + '.metadata')
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text(revision + '\n' + (etag or hashlib.sha256(data).hexdigest()) + '\n1.0\n')

    def test_lfs_and_git_etags_authenticate_exact_revision_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_file(root, 'weight.bin', b'weight', render.MODEL_REVISION)
            data = b'{"model":"test"}'
            etag = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
            self.make_file(root, 'config.json', data, render.MODEL_REVISION, etag)
            actual = render.verify_assets(root, {'weight.bin': 6, 'config.json': None}, render.MODEL_REVISION)
            self.assertEqual(actual['weight.bin'], hashlib.sha256(b'weight').hexdigest())
            (root / 'weight.bin').write_bytes(b'change')
            with self.assertRaisesRegex(render.RenderFailure, 'hash_mismatch'):
                render.verify_assets(root, {'weight.bin': 6}, render.MODEL_REVISION)

    def test_revision_wrong_size_missing_metadata_and_symlink_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_file(root, 'weight', b'abc', 'wrong-revision')
            with self.assertRaisesRegex(render.RenderFailure, 'revision'):
                render.verify_assets(root, {'weight': 3}, render.MODEL_REVISION)
            with self.assertRaisesRegex(render.RenderFailure, 'size'):
                render.verify_assets(root, {'weight': 4}, render.MODEL_REVISION)
            (root / '.cache/huggingface/download/weight.metadata').unlink()
            with self.assertRaises(render.RenderFailure):
                render.verify_assets(root, {'weight': 3}, render.MODEL_REVISION)
            (root / 'link').symlink_to(root / 'weight')
            with self.assertRaises(render.RenderFailure):
                render.verify_assets(root, {'link': 3}, render.MODEL_REVISION)

    def test_explicit_manifest_must_cover_every_file_with_exact_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'weight').write_bytes(b'abc')
            h = hashlib.sha256(b'abc').hexdigest()
            self.assertEqual(render.verify_assets(root, {'weight': 3}, render.MODEL_REVISION, {'weight': h}), {'weight': h})
            for hashes in ({}, {'weight': '0' * 64}, {'weight': h, 'extra': h}):
                with self.subTest(hashes=hashes), self.assertRaises(render.RenderFailure):
                    render.verify_assets(root, {'weight': 3}, render.MODEL_REVISION, hashes)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.guard = patch.object(render, 'require_guard'); self.guard.start()
        self.addCleanup(self.guard.stop)

    def test_eos_must_be_natural_last_and_below_cap(self):
        self.assertTrue(render.eos_observation([1, 2, 99], 99, 1100)['natural_eos'])
        for seq in ([1, 2, 3], [1, 99, 3], [1] * 1099 + [99]):
            self.assertFalse(render.eos_observation(list(seq), 99, 1100)['natural_eos'])

    def test_no_small_cer_exception_or_missing_critical_passes(self):
        score = render.transcript_score('가나다라마바사아자차카타파하', '가나다라마바사아자차카타파가')
        self.assertFalse(score['blocked'])  # Original 8% threshold.
        self.assertTrue(score['review_required'])
        row = dict(natural_eos=True, signal={'blocked': False}, asr=score)
        self.assertFalse(render.candidate_passed(row))
        row['asr'] = dict(blocked=False, review_required=False, character_errors=0, missing_critical=['전역증'])
        self.assertFalse(render.candidate_passed(row))
        row['asr']['missing_critical'] = []; row['asr']['character_errors'] = False
        self.assertFalse(render.candidate_passed(row))

    def test_exact_candidate_passes_once_and_retains_measured_contract(self):
        r = bare_renderer()
        r._candidate = Mock(side_effect=lambda u, d, s: generated(d, s))
        r._postprocess = Mock(side_effect=finalized)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'unit'
            receipt = r.render_unit(unit(), out)
            self.assertEqual(r._candidate.call_count, 1)
            self.assertEqual(receipt['status'], 'passed')
            self.assertTrue(receipt['production_eligible'])
            self.assertFalse(receipt['review_required'])
            self.assertEqual(receipt['sample_count'], 24000)
            self.assertEqual(receipt['render_settings_sha256'], render.identity(receipt['render_settings']))
            self.assertEqual(receipt, json.loads((out / 'receipt.json').read_text()))

    def test_failed_first_candidate_retries_only_unit_and_accepts_valid_second(self):
        r = bare_renderer()
        r._candidate = Mock(side_effect=lambda u, d, s: generated(d, s, error_count=1 if s == 5192 else 0))
        r._postprocess = Mock(side_effect=finalized)
        with tempfile.TemporaryDirectory() as tmp:
            receipt = r.render_unit(unit(), Path(tmp) / 'unit')
            self.assertEqual(r._candidate.call_count, 2)
            self.assertEqual(receipt['seed'], 7319)
            self.assertEqual(receipt['status'], 'passed')

    def test_all_mismatches_get_review_audio_never_final(self):
        r = bare_renderer()
        r._candidate = Mock(side_effect=lambda u, d, s: generated(d, s, error_count=1))
        r._postprocess = Mock(side_effect=AssertionError('must not be invoked'))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'unit'; receipt = r.render_unit(unit(), out)
            self.assertEqual(receipt['status'], 'blocked_requires_review')
            self.assertFalse(receipt['production_eligible'])
            self.assertTrue(receipt['review_only'])
            self.assertNotIn('file', receipt); self.assertNotIn('sha256', receipt)
            self.assertFalse((out / 'final.wav').exists())
            self.assertTrue((out / 'review.wav').exists())

    def test_perfect_transcript_without_eos_or_with_clipping_never_passes(self):
        for change in ({'eos': False}, {'clipped': True}):
            r = bare_renderer()
            r._candidate = Mock(side_effect=lambda u, d, s: generated(d, s, **change))
            r._postprocess = Mock()
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                receipt = r.render_unit(unit(), Path(tmp) / 'unit')
                self.assertTrue(receipt['blocked']); self.assertFalse(receipt['production_eligible'])
                r._postprocess.assert_not_called()
                if change.get('clipped'):
                    self.assertNotIn('review_file', receipt)

    def test_source_review_cannot_be_cleared_by_perfect_asr(self):
        r = bare_renderer(); u = unit(); u['reading_mode_verified'] = False
        r._candidate = Mock(side_effect=lambda u, d, s: generated(d, s))
        r._postprocess = Mock()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'unit'
            receipt = r.render_unit(u, out)
            r._candidate.assert_not_called()
            self.assertTrue(receipt['blocked'])
            self.assertIn('reading_mode_unverified', receipt['review_flags'])
            self.assertEqual(receipt['synthesis_skipped'], 'unresolved_input_review')
            self.assertEqual(receipt['candidates'], [])
            self.assertNotIn('file', receipt)
            self.assertNotIn('review_file', receipt)
            self.assertEqual(list(out.iterdir()), [out / 'receipt.json'])
            self.assertEqual(receipt['unit_key'], render.identity(dict(
                unit=u, render_settings_sha256=r.settings_sha256)))
            r._postprocess.assert_not_called()

    def test_normalization_review_blocks_before_synthesis(self):
        r = bare_renderer(); u = unit()
        u['review'] = ['normalization_ambiguous-range']
        r._candidate = Mock(side_effect=AssertionError('must not synthesize'))
        with tempfile.TemporaryDirectory() as tmp:
            receipt = r.render_unit(u, Path(tmp) / 'unit')
            r._candidate.assert_not_called()
            self.assertFalse(receipt['production_eligible'])
            self.assertFalse(receipt['automatic_checks_passed'])
            self.assertFalse(receipt['natural_eos'])
            self.assertEqual(receipt['asr']['status'], 'not_run_input_review')

    def test_postprocess_error_removes_partial_final_and_remains_blocked(self):
        r = bare_renderer()
        r._candidate = Mock(side_effect=lambda u, d, s: generated(d, s))
        def fail(candidate, u, d):
            (d / 'final.wav').write_bytes(b'partial')
            raise RuntimeError('private failure text')
        r._postprocess = Mock(side_effect=fail)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'unit'; receipt = r.render_unit(unit(), out)
            self.assertTrue(receipt['blocked']); self.assertNotIn('file', receipt)
            self.assertFalse((out / 'final.wav').exists())
            self.assertNotIn('private failure text', (out / 'receipt.json').read_text())

    def test_timeout_propagates_without_retry_or_pass_receipt(self):
        r = bare_renderer(); r._candidate = Mock(side_effect=TimeoutError('deadline'))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'unit'
            with self.assertRaises(TimeoutError): r.render_unit(unit(), out)
            self.assertEqual(r._candidate.call_count, 1)
            self.assertFalse((out / 'receipt.json').exists())

    def test_unexpected_native_failure_is_fixed_code_and_never_passed(self):
        r = bare_renderer(); r._candidate = Mock(side_effect=RuntimeError('SECRET SOURCE TEXT'))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'unit'; receipt = r.render_unit(unit(), out)
            self.assertTrue(receipt['blocked']); self.assertNotIn('review_file', receipt)
            self.assertNotIn('SECRET SOURCE TEXT', (out / 'receipt.json').read_text())

    def test_stale_outputs_symlinks_and_mutated_settings_are_rejected(self):
        r = bare_renderer()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); out = base / 'unit'; out.mkdir(); (out / 'final.wav').write_bytes(b'old BF16')
            with self.assertRaisesRegex(render.RenderFailure, 'not_fresh'): r.render_unit(unit(), out)
            (base / 'link').symlink_to(out, target_is_directory=True)
            with self.assertRaisesRegex(render.RenderFailure, 'symlink'): r.render_unit(unit(), base / 'link')
            r.settings['dtype'] = 'bfloat16'
            with self.assertRaisesRegex(render.RenderFailure, 'mutated'): r.render_unit(unit(), base / 'fresh')


class GuardTests(unittest.TestCase):
    def test_host_namespace_and_keys_are_rejected(self):
        with patch.dict(render.os.environ, {'BORA_HOST_NETNS': 'net:[123]',
                    'ADMIN_LAW_PREMIUM_SOURCE_KEY_B64': 'not-a-real-key'}, clear=True):
            with self.assertRaisesRegex(render.RenderFailure, 'keys_in_renderer'):
                render.require_guard()
        with patch.dict(render.os.environ, {'BORA_HOST_NETNS': 'net:[123]'}, clear=True), \
             patch.object(render.os, 'getuid', return_value=1000), \
             patch.object(render.os, 'geteuid', return_value=1000), \
             patch.object(render.os, 'readlink', return_value='net:[123]'):
            with self.assertRaisesRegex(render.RenderFailure, 'offline_namespace'):
                render.require_guard()

    def test_effective_root_is_rejected_even_with_real_user_uid(self):
        with patch.dict(render.os.environ, {}, clear=True), \
             patch.object(render.os, 'getuid', return_value=1000), \
             patch.object(render.os, 'geteuid', return_value=0):
            with self.assertRaisesRegex(render.RenderFailure, 'unprivileged'):
                render.require_guard()


class WarmEngineTests(unittest.TestCase):
    def test_constructor_loads_fp32_model_once_for_multiple_units(self):
        torch = types.SimpleNamespace(float32='fp32', set_num_threads=Mock(),
                                      get_num_threads=lambda: 4)
        parameter = types.SimpleNamespace(dtype='fp32', is_floating_point=lambda: True)
        model = types.SimpleNamespace(model=types.SimpleNamespace(parameters=lambda: [parameter]))
        qwen = Mock(); qwen.from_pretrained.return_value = model
        whisper = Mock()
        modules = {'onnxruntime': types.SimpleNamespace(disable_telemetry_events=Mock()),
                   'torch': torch, 'soundfile': SyntheticWavIO,
                   'qwen_tts': types.SimpleNamespace(Qwen3TTSModel=qwen),
                   'faster_whisper': types.SimpleNamespace(WhisperModel=whisper)}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'config.json').write_text(json.dumps(dict(
                model_type='qwen3_tts', tts_model_type='custom_voice', tts_model_size='1b7')))
            with patch.object(render, 'require_guard'), patch.object(render, 'verify_assets', return_value={'a': '0' * 64}), \
                 patch.object(render.importlib.metadata, 'version', side_effect=lambda n: render.EXPECTED_PACKAGES[n]), \
                 patch.object(render.shutil, 'which', return_value='/synthetic/ffmpeg'), \
                 patch.object(render.subprocess, 'run', return_value=types.SimpleNamespace(stdout='ffmpeg synthetic\n')), \
                 patch.object(render, 'quiet_native', side_effect=contextlib.nullcontext), \
                 patch.dict(sys.modules, modules):
                renderer = render.FP32Renderer(root, root)
                renderer._candidate = Mock(side_effect=lambda u, d, s: generated(d, s))
                renderer._postprocess = Mock(side_effect=finalized)
                first = renderer.render_unit(unit(), root / 'out0')
                second = renderer.render_unit(unit(ident='TB-001:p0001:u000'), root / 'out1')
            qwen.from_pretrained.assert_called_once()
            whisper.assert_called_once()
            self.assertEqual(qwen.from_pretrained.call_args.kwargs['dtype'], 'fp32')
            self.assertEqual(qwen.from_pretrained.call_args.kwargs['attn_implementation'], 'sdpa')
            self.assertTrue(qwen.from_pretrained.call_args.kwargs['local_files_only'])
            torch.set_num_threads.assert_called_once_with(4)
            self.assertEqual(first['render_settings_sha256'], second['render_settings_sha256'])
            self.assertNotEqual(first['unit_key'], second['unit_key'])

    def test_generate_uses_explicit_profile_and_restores_eos_observer(self):
        r = bare_renderer(); original = Mock(return_value=types.SimpleNamespace(sequences=[np.array([1, 2, 99])]))
        talker = types.SimpleNamespace(generate=original)
        calls = []
        def generate(**kwargs):
            calls.append(kwargs); talker.generate()
            return [synthetic_wave()], 24000
        r.model = types.SimpleNamespace(model=types.SimpleNamespace(talker=talker,
            config=types.SimpleNamespace(talker_config=types.SimpleNamespace(codec_eos_token_id=99))),
            generate_custom_voice=generate)
        r.torch = types.SimpleNamespace(manual_seed=Mock(), inference_mode=contextlib.nullcontext)
        r._recognize = Mock(return_value=dict(recognized=unit()['spoken'], words=[]))
        with tempfile.TemporaryDirectory() as tmp, patch.object(render, 'quiet_native', side_effect=contextlib.nullcontext):
            result = r._candidate(unit(), Path(tmp), 5192)
        self.assertIs(talker.generate, original)
        self.assertTrue(render.candidate_passed(result))
        for key, value in dict(render.SAMPLING, max_new_tokens=1100).items():
            self.assertEqual(calls[0][key], value)
        self.assertEqual(calls[0]['instruct'], render.STYLES['explain'])


if __name__ == '__main__':
    unittest.main()
