"""Public-only SenseVoiceSmall CPU compatibility and transcription diagnostic.

Authenticates the existing four-WAV public experiment, then recognizes its two
eager references. Does not open private sources, generate audio or approve QA.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import signal
import time

import cpu_public_asr_check as public

SCHEMA = 'bora-public-sensevoice-check/v1'
REQUEST = Path(__file__).with_name('cpu-public-sensevoice-request.json')
ASR_REQUEST_SHA256 = '318d737f135fe68a408867e7a209c550dac9c64bdb539f2f53a7027b77fd143a'
AUTHENTICATOR_SHA256 = '929c8eaee76406d4b5067798d709da409f8b90eff9860861e5fb500969636fdc'
MODEL_REPO = 'csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17'
MODEL_REVISION = '2365baeacb507f821a0c8120fcee3d484dba7a07'
MODEL_FILES = {
    'model.int8.onnx': dict(size_bytes=239233841,
        sha256='c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51'),
    'tokens.txt': dict(size_bytes=315894,
        sha256='f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc'),
}
TOKEN_BLOB_SHA1 = '2cfc92fc2ff26aaa690b7c01fd96b41109413881'
RUNTIME = {'sherpa-onnx': '2.0.1', 'sherpa-onnx-core': '2.0.1',
    'numpy': '2.5.4', 'soundfile': '0.14.0', 'scipy': '1.18.1',
    'huggingface-hub': '0.36.2'}
OPTIONS = dict(num_threads=4, sample_rate=16000, feature_dim=80,
    decoding_method='greedy_search', debug=False, provider='cpu',
    language='ko', use_itn=False)
CALL_SECONDS = 240


def request_value():
    return dict(schema=SCHEMA, request_id='public-sensevoice-20261114-01',
        source_run_id=public.SOURCE_RUN, source_commit=public.SOURCE_SHA,
        artifact_id=14089353182, public_input_request_sha256=ASR_REQUEST_SHA256,
        authenticator_sha256=AUTHENTICATOR_SHA256,
        model=dict(repo=MODEL_REPO, revision=MODEL_REVISION, files=copy.deepcopy(MODEL_FILES),
            tokens_git_blob_sha1=TOKEN_BLOB_SHA1),
        runtime=copy.deepcopy(RUNTIME), recognition_options=copy.deepcopy(OPTIONS),
        synthesis=False, private_inputs=False, secrets=False, promotion=False,
        model_or_audio_upload=False)


def validate_request(value):
    public.keyless()
    public.require(value == request_value())
    public.require(all(type(pin['size_bytes']) is int and pin['size_bytes'] > 0
        and public.HASH.fullmatch(pin['sha256']) and pin['sha256'] != '0'*64
        for pin in MODEL_FILES.values()))
    return value


def public_scope(directory):
    """Use the frozen, ready id03 request and its exact four-file authenticator."""
    public.keyless()
    public.require(public.digest(public.REQUEST) == ASR_REQUEST_SHA256
        and public.digest(Path(public.__file__)) == AUTHENTICATOR_SHA256)
    request = public.validate_request(json.loads(public.REQUEST.read_text()))
    public.require(request['request_id'] == 'public-long-asr-20261114-03'
        and request['artifact_id'] == 14089353182)
    return public.validate_inputs(directory, request)


def verify_model(directory):
    public.keyless()
    root = Path(directory)
    public.require(root.is_dir() and not any(p.is_symlink() for p in (root, *root.parents)))
    observed = {}
    for name, pin in MODEL_FILES.items():
        path = public.regular(root/name)
        public.require(path.stat().st_size == pin['size_bytes'])
        observed[name] = public.digest(path)
        public.require(observed[name] == pin['sha256'])
        if name == 'tokens.txt':
            blob = b'blob '+str(pin['size_bytes']).encode()+b'\0'+path.read_bytes()
            public.require(hashlib.sha1(blob).hexdigest() == TOKEN_BLOB_SHA1)
    return observed


def download_model(directory):
    public.keyless(); root = Path(directory)
    public.require(not root.exists())
    from huggingface_hub import snapshot_download
    snapshot_download(MODEL_REPO, revision=MODEL_REVISION, local_dir=str(root),
        allow_patterns=list(MODEL_FILES), max_workers=2)
    verify_model(root)


def load_recognizer(directory):
    public.keyless()
    from sherpa_onnx import OfflineRecognizer
    root = Path(directory)
    return OfflineRecognizer.from_sense_voice(model=str(root/'model.int8.onnx'),
        tokens=str(root/'tokens.txt'), **OPTIONS)


def recognize(row, recognizer):
    public.keyless()
    fixture = public.long.fixtures()[row['fixture']]
    public.require(row['fixture'] in (0, 1) and row['expected_spoken'] == fixture['spoken']
        and row['critical_terms'] == list(public.CRITICAL[row['fixture']])
        and public.digest(row['path']) == row['wav_sha256'])
    started = time.monotonic()
    samples = public.recognition_input(row['path'], 'small')
    stream = recognizer.create_stream()
    stream.accept_waveform(16000, samples)
    recognizer.decode_stream(stream)
    recognized = stream.result.text
    score = public.diagnostic_score(fixture['spoken'], recognized, row['critical_terms'])
    public.require(public.digest(row['path']) == row['wav_sha256'])
    return dict(fixture=row['fixture'], expected_public_spoken=fixture['spoken'],
        critical_terms=row['critical_terms'], wav_sha256=row['wav_sha256'],
        pcm_sha256=row['pcm_sha256'], asr_seconds=time.monotonic()-started, **score)


def inspect(directory, model_directory, output, request):
    validate_request(request); selected = public_scope(directory)
    public.require([row['fixture'] for row in selected] == [0, 1] and not Path(output).exists())
    runtime = {name: importlib.metadata.version(name) for name in RUNTIME}
    public.require(runtime == RUNTIME)
    result = dict(schema=SCHEMA, status='public_sensevoice_in_progress',
        request=request, runtime_packages=runtime, recognition_options=copy.deepcopy(OPTIONS),
        input_representation=public.ASR_SETTINGS['small']['input_representation'],
        model_family='SenseVoiceSmall_CTC', raw_gate='Existing canonical zero errors and no missing critical terms.',
        normalized_gate='Separate pronunciation-normalization diagnostic; never a replacement for raw QA.',
        exact_public_pcm_verified=True, stage='authenticating_model', current_fixture=None,
        rows=[], synthesized=0, private_inputs_used=False, secrets_used=False,
        promotion_performed=False, production_eligible=False, human_listening_verified=False,
        model_or_audio_uploaded=False,
        caveat='Two fixed public fixtures only. A different ASR architecture and explicit preprocessing are being checked, not production approval or model-only causal performance. Execution times are observations, not an ASR speed comparison.')
    def deadline(*args):
        raise TimeoutError('public_sensevoice_call_timeout')
    previous = signal.signal(signal.SIGALRM, deadline)
    try:
        public.dump(output, result)
        with public.offline():
            result['model_assets_sha256'] = verify_model(model_directory)
            result['stage'] = 'loading_recognizer'; public.dump(output, result)
            recognizer = load_recognizer(model_directory)
            for row in selected:
                result.update(stage='recognizing_fixture', current_fixture=row['fixture'])
                public.dump(output, result); signal.alarm(CALL_SECONDS)
                try:
                    result['rows'].append(recognize(row, recognizer)); public.dump(output, result)
                finally:
                    signal.alarm(0)
        result.update(status='complete_public_sensevoice_no_promotion', stage='complete', current_fixture=None)
        public.dump(output, result)
        print(json.dumps(dict(status=result['status'], checks=len(result['rows']),
            raw_exact=sum(row['raw_canonical_strict']['exact'] for row in result['rows']),
            normalized_exact=sum(row['pronunciation_normalized']['exact'] for row in result['rows']),
            synthesized=0, promoted=0)), flush=True)
    except Exception as error:
        result.update(status='public_sensevoice_failed_partial_not_approval',
            failure=public.public_failure(error))
        public.dump(output, result); raise
    finally:
        signal.alarm(0); signal.signal(signal.SIGALRM, previous)


def main():
    os.umask(0o077); public.keyless()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('download-model', 'inspect'))
    parser.add_argument('--model', required=True); parser.add_argument('--input'); parser.add_argument('--output')
    args = parser.parse_args(); request = validate_request(json.loads(REQUEST.read_text()))
    if args.mode == 'download-model':
        public.require(args.input is None and args.output is None); download_model(args.model)
    else:
        public.require(bool(args.input) and bool(args.output))
        inspect(args.input, args.model, args.output, request)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('{"status":"failed","reason":"public_sensevoice_validation_failed"}', flush=True)
        raise SystemExit(1)
