import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

import cpu_public_asr_check as check


class PublicAsrTests(unittest.TestCase):
    def wav(self, fixture=0):
        pcm = struct.pack('<4f', 0.1, -0.1, 0.05+fixture*0.01, -0.05)
        chunks = b'fmt '+struct.pack('<IHHIIHH', 16, 3, 1, 24000, 96000, 4, 32)
        chunks += b'data'+struct.pack('<I', len(pcm))+pcm
        return b'RIFF'+struct.pack('<I', len(chunks)+4)+b'WAVE'+chunks, pcm

    def fixture(self, directory):
        root = Path(directory); rows = []
        for index, (mode, fixture) in enumerate(check.long.SCHEDULE):
            wave, pcm = self.wav(fixture)
            path = root/check.WAV_NAMES[index]; path.write_bytes(wave)
            tokens = [[7, 8, fixture, 9]]
            row = dict(call_index=index, mode=mode, **check.long.fixtures()[fixture],
                generation_args=check.long.generation_args(fixture),
                tts_seconds=10.0, audio_seconds=4/24000, sample_rate=24000,
                pcm_samples=4, signal_valid=True, natural_eos=True,
                within_production_part_limit=True, comparison_pending=False,
                exact_pcm_equal=True, exact_sequence_equal=True, exact_rate_and_samples=True,
                talker_sequences=tokens,
                talker_sequence_sha256=hashlib.sha256(json.dumps(tokens,separators=(',', ':')).encode()).hexdigest(),
                wav_file=path.name, wav_sha256=check.digest(path),
                pcm_sha256=hashlib.sha256(pcm).hexdigest(),
                compiled_graph_invocations_during_call=20 if mode == check.long.COMPILED else 0,
                new_graphs_compiled_during_call=3 if index == 1 else 0)
            rows.append(row)
        summary = check.long.summarize(rows, {'backend_graphs_compiled': 3}, 0.1)
        check.dump(root/'summary.json', summary)
        return check.request_value(123456, check.file_pins(root))

    def test_only_complete_public_four_wav_pairs_select_two_eager_references(self):
        with tempfile.TemporaryDirectory() as directory:
            request = self.fixture(directory)
            rows = check.validate_inputs(directory, request)
            self.assertEqual([row['fixture'] for row in rows], [0, 1])
            self.assertEqual([row['path'].name for row in rows], [check.WAV_NAMES[0], check.WAV_NAMES[3]])
            self.assertEqual([row['expected_spoken'] for row in rows],
                             [row['spoken'] for row in check.long.fixtures()])

    def test_placeholder_and_scope_or_configuration_changes_fail_closed(self):
        with self.assertRaises(ValueError):
            check.validate_request(check.request_value())
        with tempfile.TemporaryDirectory() as directory:
            request = self.fixture(directory)
            for key, value in [('source_run_id', 1), ('source_commit', 'a'*40),
                               ('promotion', True), ('private_inputs', True), ('artifact_id', True)]:
                changed = copy.deepcopy(request); changed[key] = value
                with self.subTest(key=key), self.assertRaises(ValueError):
                    check.validate_request(changed)
            changed = copy.deepcopy(request)
            changed['fixtures'][1]['spoken'] = 'another public or private sentence'
            with self.assertRaises(ValueError):
                check.validate_request(changed)

    def test_all_four_wav_hashes_not_only_selected_audio_are_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            request = self.fixture(directory)
            path = Path(directory)/check.WAV_NAMES[2]
            path.write_bytes(path.read_bytes()+b'changed')
            with self.assertRaises(ValueError):
                check.validate_inputs(directory, request)

    def test_pinned_but_unequal_actual_pcm_cannot_trust_summary_booleans(self):
        with tempfile.TemporaryDirectory() as directory:
            request = self.fixture(directory); root = Path(directory)
            path = root/check.WAV_NAMES[2]
            wave, pcm = self.wav(0); path.write_bytes(wave)
            summary = json.loads((root/'summary.json').read_text())
            summary['rows'][2].update(wav_sha256=check.digest(path),
                                     pcm_sha256=hashlib.sha256(pcm).hexdigest())
            check.dump(root/'summary.json', summary)
            request['files'] = check.file_pins(root)
            with self.assertRaises(ValueError):
                check.validate_inputs(directory, request)

    def test_incomplete_wrong_source_symlink_and_extra_wav_are_rejected(self):
        for kind in ('partial', 'source', 'symlink', 'extra'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                request = self.fixture(directory); root = Path(directory)
                if kind in ('partial', 'source'):
                    summary = json.loads((root/'summary.json').read_text())
                    if kind == 'partial': summary['complete'] = False
                    else: summary['rows'][0]['source'] = 'unapproved input'
                    check.dump(root/'summary.json', summary)
                    request['files'] = check.file_pins(root)
                elif kind == 'symlink':
                    path = root/check.WAV_NAMES[1]; target = root/'replacement'
                    path.rename(target); path.symlink_to(target)
                else:
                    (root/'private.wav').write_bytes(self.wav()[0])
                with self.assertRaises(ValueError):
                    check.validate_inputs(directory, request)

    def test_normalization_diagnostic_cannot_replace_raw_zero_error_gate(self):
        score = check.diagnostic_score('세 시간 스물다섯 명', '3시간 25명', ['세 시간', '스물다섯 명'])
        self.assertFalse(score['raw_canonical_strict']['exact'])
        self.assertTrue(score['pronunciation_normalized']['exact'])
        self.assertFalse(score['production_eligible']); self.assertFalse(score['promotion_performed'])
        bad = check.diagnostic_score('세 시간 스물다섯 명', '3시간 26명', ['세 시간', '스물다섯 명'])
        self.assertFalse(bad['pronunciation_normalized']['exact'])
        self.assertTrue(bad['pronunciation_normalized']['missing_critical'])
        flagged = check.diagnostic_score('테스트', 'test', ['테스트'])
        self.assertFalse(flagged['pronunciation_normalized']['exact'])
        self.assertTrue(flagged['pronunciation_normalized']['review_flags'])

    def test_asr_receives_only_audio_and_explicit_unprompted_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            request = self.fixture(directory)
            selected = check.validate_inputs(directory, request)
            for name in check.MODELS:
                calls = []
                audio_input = object()
                class Model:
                    def transcribe(self, path, **kwargs):
                        calls.append((path, kwargs))
                        return iter([SimpleNamespace(text='원문')]), None
                with patch.object(check, 'recognition_input', return_value=audio_input) as prepare:
                    rows = check.recognize(selected, Model(), name)
                    self.assertEqual(prepare.call_args_list[0].args, (selected[0]['path'], name))
                    self.assertEqual(prepare.call_args_list[1].args, (selected[1]['path'], name))
                self.assertEqual(len(rows), 2)
                for path, options in calls:
                    self.assertIs(path, audio_input)
                    self.assertEqual(options, dict(check.TRANSCRIBE,
                        word_timestamps=check.ASR_SETTINGS[name]['word_timestamps']))
                    self.assertNotIn('initial_prompt', options)
                    self.assertNotIn('hotwords', options)
                    self.assertFalse(options['condition_on_previous_text'])
                    self.assertFalse(options['vad_filter'])

    def test_small_uses_exact_production_resampling_and_turbo_retains_wav_path(self):
        # No third-party packages are needed before workflow installation.
        # These stubs verify the actual input values, rational factors and cast.
        wave = SimpleNamespace(ndim=1, dtype='float32')
        converted = object(); calls = []
        class Resampled:
            def astype(self, dtype):
                calls.append(('cast', dtype)); return converted
        def read(path, **kwargs):
            calls.append(('read', path, kwargs)); return wave, 24000
        def resample(samples, up, down):
            calls.append(('resample', samples, up, down)); return Resampled()
        scipy = ModuleType('scipy'); signal = ModuleType('scipy.signal')
        signal.resample_poly = resample; scipy.signal = signal
        modules = {'numpy': SimpleNamespace(dtype=lambda name: name),
            'soundfile': SimpleNamespace(read=read), 'scipy': scipy, 'scipy.signal': signal}
        with patch.dict(sys.modules, modules):
            self.assertIs(check.recognition_input(Path('/public.wav'), 'small'), converted)
            self.assertEqual(calls[0], ('read', '/public.wav', {'dtype': 'float32'}))
            self.assertEqual(calls[1], ('resample', wave, 2, 3))
            self.assertEqual(calls[2], ('cast', 'float32'))
            calls.clear()
            self.assertEqual(check.recognition_input(Path('/public.wav'), 'turbo'), '/public.wav')
            self.assertEqual(calls, [])
            wave.ndim = 2
            with self.assertRaises(ValueError): check.recognition_input('/public.wav', 'small')
            wave.ndim = 1; wave.dtype = 'float64'
            with self.assertRaises(ValueError): check.recognition_input('/public.wav', 'small')
        self.assertEqual(check.ASR_SETTINGS['small']['cpu_threads'], 1)
        self.assertTrue(check.ASR_SETTINGS['small']['word_timestamps'])
        self.assertEqual(check.ASR_SETTINGS['turbo']['cpu_threads'], 4)
        self.assertFalse(check.ASR_SETTINGS['turbo']['word_timestamps'])
        self.assertEqual(check.RUNTIME_PINS['av'], '18.1.0')

    def test_keys_network_and_wav_mutation_are_blocked(self):
        for secret in check.SECRETS:
            with patch.dict(os.environ, {secret: 'must-not-use'}):
                with self.assertRaises(ValueError): check.keyless()
                with self.assertRaises(ValueError): check.download_models('/unused')
        original = socket.create_connection
        with check.offline():
            with self.assertRaises(RuntimeError): socket.create_connection(('example.com', 443))
            with socket.socket() as client:
                with self.assertRaises(RuntimeError): client.connect_ex(('127.0.0.1', 80))
        self.assertIs(socket.create_connection, original)
        with tempfile.TemporaryDirectory() as directory:
            selected = check.validate_inputs(directory, self.fixture(directory))
            class Model:
                def transcribe(self, path, **kwargs):
                    Path(path).write_bytes(b'changed')
                    return iter([SimpleNamespace(text='원문')]), None
            with self.assertRaises(ValueError): check.recognize(selected, Model(), 'turbo')

    def test_float_wav_rejects_nonfinite_signal_wrong_rate_and_clipping(self):
        wave, _ = self.wav()
        invalid = bytearray(wave); struct.pack_into('<I', invalid, 24, 16000)
        with self.assertRaises(ValueError): check.float_pcm(bytes(invalid))
        invalid = bytearray(wave); struct.pack_into('<f', invalid, len(invalid)-4, float('nan'))
        with self.assertRaises(ValueError): check.float_pcm(bytes(invalid))
        invalid = bytearray(wave); struct.pack_into('<f', invalid, len(invalid)-4, 1.0)
        with self.assertRaises(ValueError): check.float_pcm(bytes(invalid))

    def test_public_exception_details_are_keyless_bounded_and_have_no_locals(self):
        def nested(depth):
            private_local = 'this local must never be exported'
            if depth: return nested(depth-1)
            raise RuntimeError('public model error '+('x'*5000))
        try:
            nested(15)
        except RuntimeError as error:
            report = check.public_failure(error)
            self.assertEqual(report['exception_type'], 'RuntimeError')
            self.assertEqual(report['exception_module'], 'builtins')
            self.assertEqual(len(report['message']), 1200)
            self.assertTrue(report['message_truncated'])
            self.assertEqual(len(report['frames']), 10)
            self.assertTrue(all(set(frame) == {'file', 'line'}
                and '/' not in frame['file'] and frame['line'] > 0 for frame in report['frames']))
            encoded = json.dumps(report)
            self.assertNotIn('private_local', encoded)
            self.assertNotIn('this local must never be exported', encoded)
            self.assertFalse(report['includes_locals']); self.assertFalse(report['includes_environment'])
            for secret in check.SECRETS:
                with patch.dict(os.environ, {secret: 'not-public'}):
                    with self.assertRaises(ValueError): check.public_failure(error)

    def test_failure_keeps_completed_public_rows_and_exact_loading_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); request = self.fixture(root)
            selected = check.validate_inputs(root, request); output = root/'diagnostic.json'
            def model(path, **kwargs):
                recorded = json.loads(output.read_text())
                self.assertEqual(recorded['stage'], 'loading_model')
                if Path(path).name == 'turbo':
                    raise RuntimeError('actual public model-load failure')
                return object()
            def recognize(rows, unused_model, name):
                recorded = json.loads(output.read_text())
                self.assertEqual(recorded['stage'], 'recognizing_fixture')
                self.assertEqual(recorded['current_fixture'], rows[0]['fixture'])
                return [dict(fixture=rows[0]['fixture'], model=name, public=True)]
            with patch.object(check.importlib.metadata, 'version', side_effect=check.RUNTIME_PINS.__getitem__), \
                 patch.object(check, 'verify_model', return_value={'model.bin': 'a'*64}), \
                 patch.object(check, 'recognize', side_effect=recognize), \
                 patch.dict(sys.modules, {'faster_whisper': SimpleNamespace(WhisperModel=model)}):
                with self.assertRaises(RuntimeError):
                    check.inspect(root, root/'models', output, request)
            report = json.loads(output.read_text())
            self.assertEqual(report['runtime_packages']['av'], '18.1.0')
            self.assertEqual(len(report['rows']), 2)
            self.assertEqual(report['current_model'], 'turbo')
            self.assertIsNone(report['current_fixture'])
            self.assertEqual(report['stage'], 'loading_model')
            self.assertEqual(report['failure']['message'], 'actual public model-load failure')
            self.assertEqual(report['failure']['exception_type'], 'RuntimeError')
            self.assertFalse(report['production_eligible'])


if __name__ == '__main__':
    unittest.main()
