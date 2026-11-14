import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cpu_public_sensevoice_check as sense


class PublicSenseVoiceTests(unittest.TestCase):
    def row(self, directory, fixture=0):
        path = Path(directory)/f'public-{fixture}.wav'
        path.write_bytes(b'authenticated elsewhere public sample')
        return dict(fixture=fixture, path=path, wav_sha256=sense.public.digest(path),
            pcm_sha256='a'*64, expected_spoken=sense.public.long.fixtures()[fixture]['spoken'],
            critical_terms=list(sense.public.CRITICAL[fixture]))

    def test_request_is_exact_public_scope_model_and_runtime(self):
        request = sense.request_value(); sense.validate_request(request)
        for field, value in [('private_inputs', True), ('promotion', True), ('synthesis', True),
                             ('artifact_id', 1), ('public_input_request_sha256', 'a'*64)]:
            changed = copy.deepcopy(request); changed[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): sense.validate_request(changed)
        for field, value in [('language', 'auto'), ('use_itn', True), ('provider', 'cuda')]:
            changed = copy.deepcopy(request); changed['recognition_options'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): sense.validate_request(changed)
        changed = copy.deepcopy(request); changed['model']['revision'] = 'main'
        with self.assertRaises(ValueError): sense.validate_request(changed)
        self.assertEqual(request['runtime']['sherpa-onnx'], '2.0.1')
        self.assertEqual(request['runtime']['sherpa-onnx-core'], '2.0.1')
        self.assertFalse(any('torch' in key or 'ctranslate' in key for key in request['runtime']))

    def test_frozen_scope_delegates_full_four_wav_authentication_and_rejects_changes(self):
        selected = [dict(fixture=0), dict(fixture=1)]
        with patch.object(sense.public, 'validate_inputs', return_value=selected) as verify:
            self.assertEqual(sense.public_scope('/public-input'), selected)
            request = verify.call_args.args[1]
            self.assertEqual(request['request_id'], 'public-long-asr-20261114-03')
            self.assertEqual(request['artifact_id'], 14089353182)
            self.assertEqual(len(request['files']), 5)
            self.assertEqual(len([x for x in request['files'] if x.endswith('.wav')]), 4)
        for failure in ('request', 'code'):
            def digest(path):
                if Path(path) == sense.public.REQUEST:
                    return 'a'*64 if failure == 'request' else sense.ASR_REQUEST_SHA256
                return 'a'*64 if failure == 'code' else sense.AUTHENTICATOR_SHA256
            with patch.object(sense.public, 'digest', side_effect=digest), \
                 patch.object(sense.public, 'validate_inputs') as verify:
                with self.assertRaises(ValueError): sense.public_scope('/public-input')
                verify.assert_not_called()
        with patch.object(sense.public, 'validate_inputs', side_effect=ValueError('tampered public WAV')):
            with self.assertRaises(ValueError): sense.public_scope('/public-input')

    def test_both_model_files_require_exact_size_sha_and_token_git_blob(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); data = {'model.int8.onnx': b'public model', 'tokens.txt': b'public token 1\n'}
            pins = {}
            for name, value in data.items():
                (root/name).write_bytes(value)
                pins[name] = dict(size_bytes=len(value), sha256=hashlib.sha256(value).hexdigest())
            token_id = hashlib.sha1(b'blob '+str(len(data['tokens.txt'])).encode()+b'\0'+data['tokens.txt']).hexdigest()
            with patch.object(sense, 'MODEL_FILES', pins), patch.object(sense, 'TOKEN_BLOB_SHA1', token_id):
                self.assertEqual(sense.verify_model(root), {k:v['sha256'] for k,v in pins.items()})
                for name in pins:
                    (root/name).write_bytes(data[name][::-1])
                    with self.subTest(name=name), self.assertRaises(ValueError): sense.verify_model(root)
                    (root/name).write_bytes(data[name])
                with patch.object(sense, 'TOKEN_BLOB_SHA1', 'a'*40):
                    with self.assertRaises(ValueError): sense.verify_model(root)
                path = root/'tokens.txt'; path.unlink(); path.symlink_to(root/'model.int8.onnx')
                with self.assertRaises(ValueError): sense.verify_model(root)

    def test_runtime_constructor_has_only_cpu_korean_ctc_options(self):
        calls = []
        class OfflineRecognizer:
            @staticmethod
            def from_sense_voice(**kwargs):
                calls.append(kwargs); return 'recognizer'
        with patch.dict(sys.modules, {'sherpa_onnx': SimpleNamespace(OfflineRecognizer=OfflineRecognizer)}):
            self.assertEqual(sense.load_recognizer('/public-model'), 'recognizer')
        self.assertEqual(calls, [dict(sense.OPTIONS, model='/public-model/model.int8.onnx', tokens='/public-model/tokens.txt')])
        self.assertEqual(sense.OPTIONS['language'], 'ko'); self.assertFalse(sense.OPTIONS['use_itn'])
        self.assertEqual(sense.OPTIONS['num_threads'], 4)
        for key in ('initial_prompt', 'hotwords', 'vad_filter', 'expected_text'):
            self.assertNotIn(key, calls[0])

    def test_model_gets_only_16khz_audio_and_diagnostics_keep_raw_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            row = self.row(directory, 1); samples = object(); calls = []
            stream = SimpleNamespace(result=SimpleNamespace(text=sense.public.long.SOURCES[1]),
                accept_waveform=lambda rate, audio: calls.append(('audio', rate, audio)))
            recognizer = SimpleNamespace(create_stream=lambda: stream,
                decode_stream=lambda value: calls.append(('decode', value)))
            with patch.object(sense.public, 'recognition_input', return_value=samples) as prepare:
                result = sense.recognize(row, recognizer)
            prepare.assert_called_once_with(row['path'], 'small')
            self.assertEqual(calls, [('audio', 16000, samples), ('decode', stream)])
            self.assertFalse(result['raw_canonical_strict']['exact'])
            self.assertTrue(result['pronunciation_normalized']['exact'])
            self.assertFalse(result['production_eligible']); self.assertFalse(result['promotion_performed'])
            stream.result.text = sense.public.long.SOURCES[1].replace('3시간', '4시간')
            with patch.object(sense.public, 'recognition_input', return_value=samples):
                wrong = sense.recognize(row, recognizer)
            self.assertFalse(wrong['pronunciation_normalized']['exact'])
            self.assertTrue(wrong['pronunciation_normalized']['missing_critical'])

    def test_altered_source_or_audio_cannot_reach_recognizer(self):
        with tempfile.TemporaryDirectory() as directory:
            row = self.row(directory)
            recognizer = SimpleNamespace(create_stream=lambda: self.fail('inference forbidden'))
            row['expected_spoken'] = 'another sentence'
            with self.assertRaises(ValueError): sense.recognize(row, recognizer)
            row = self.row(directory); row['path'].write_bytes(b'changed audio')
            with self.assertRaises(ValueError): sense.recognize(row, recognizer)

    def test_keys_block_download_constructor_diagnostics_and_requests(self):
        for name in sense.public.SECRETS:
            with patch.dict(os.environ, {name: 'must not be used'}):
                for action in (lambda: sense.download_model('/unused'),
                               lambda: sense.load_recognizer('/unused'),
                               lambda: sense.validate_request(sense.request_value())):
                    with self.assertRaises(ValueError): action()

    def test_partial_failure_preserves_first_public_result_without_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); rows = [self.row(root, index) for index in (0, 1)]
            output = root/'public.json'
            def recognize(row, model):
                if row['fixture'] == 1: raise RuntimeError('public CTC API diagnostic')
                return dict(fixture=0, production_eligible=False)
            with patch.object(sense, 'public_scope', return_value=rows), \
                 patch.object(sense.importlib.metadata, 'version', side_effect=sense.RUNTIME.__getitem__), \
                 patch.object(sense, 'verify_model', return_value={'model.int8.onnx': 'a'*64}), \
                 patch.object(sense, 'load_recognizer', return_value=object()), \
                 patch.object(sense, 'recognize', side_effect=recognize):
                with self.assertRaises(RuntimeError): sense.inspect(root, root/'model', output, sense.request_value())
            report = json.loads(output.read_text())
            self.assertEqual(len(report['rows']), 1)
            self.assertEqual(report['current_fixture'], 1)
            self.assertEqual(report['stage'], 'recognizing_fixture')
            self.assertEqual(report['failure']['message'], 'public CTC API diagnostic')
            self.assertFalse(report['production_eligible']); self.assertEqual(report['synthesized'], 0)


if __name__ == '__main__':
    unittest.main()
