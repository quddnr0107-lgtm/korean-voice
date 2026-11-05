#!/usr/bin/env python3
"""Second-model transcription of every reviewed-sample disagreement; never approves.

The exact published MP3 is decoded with the previously used small Whisper model.
Targets, normalization and legal-token comparisons remain unchanged. Both models
are from the Whisper family, so agreement is supporting evidence, not proof.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import importlib.metadata
import io
import json
import os
from pathlib import Path
import re
import time

import reader_assistance as R
import reader_assistance_asr as A
import reader_assistance_subset as S
import reader_assistance_verify as V
import reader_assistance_final_verify as F
import reader_assistance_revision_verify as M

MODEL_REPOSITORY = 'Systran/faster-whisper-small'
MODEL_REVISION = '536b0662742c02347bc0e980a01041f333bce120'
REPORT_SCHEMA = 'reader-assistance-asr-crosscheck.v1'
REPORT_BRANCH = 'data/reader-assistance-f1-crosscheck'


def disagreement_keys(report, selection, profile, plan, state):
    R.require(report.get('coverage', {}).get('allSelectedClipsAssessed') is True and
              report.get('directListeningCompleted') is False, 'Finish the fixed primary sample before crosschecking it')
    results = V.validate_cached(report, selection, profile, plan, state)
    R.require(set(results) == {entry['audioKey'] for entry in selection['entries']}, 'Primary sample is incomplete')
    R.require(report.get('counts') == dict(Counter(item['status'] for item in results.values())),
              'Primary assessment counts do not match its evidence')
    return sorted(key for key, result in results.items() if result['status'] == 'review_required')


def report_path(primary_sha):
    R.require(R.HEX64.fullmatch(primary_sha), 'Pinned primary report digest required')
    return 'reader-assistance/crosscheck-report-' + primary_sha[:16] + '.json'


def load_primary(client, contract):
    R.require(contract.get('schema') == 'reader-assistance-crosscheck-input.v1' and
              re.fullmatch('[a-f0-9]{40}', contract.get('commit', '')) and
              re.fullmatch('[a-f0-9]{40}', contract.get('gitBlobSha', '')) and
              R.HEX64.fullmatch(contract.get('sha256', '')) and
              re.fullmatch(r'reader-assistance/verification-report-[a-f0-9]{16}\.json', contract.get('path', '')),
              'Crosscheck input must identify an immutable public primary report')
    endpoint = '/repos/' + R.REPOSITORY + '/contents/' + contract['path'] + '?ref=' + contract['commit']
    payload = client.request('GET', endpoint)
    R.require(payload.get('encoding') == 'base64', 'Unexpected primary report encoding')
    data = base64.b64decode(payload['content'])
    R.require(R.git_blob(data) == payload.get('sha') == contract['gitBlobSha'] and R.sha256(data) == contract['sha256'],
              'Primary report bytes differ from their immutable pin')
    return json.loads(data)


def validate_primary_selection(primary, data_bytes, stage_bytes, recipe, policy, baseline,
                               *, previous=None, catalog_bytes=None):
    """Reproduce the declared sample before a second transcriber can use it.

    A corrected report must retain the immutable earlier evidence and select
    every changed text. Its embedded selection is evidence to verify, never an
    input allowed to shrink the sample or remove an inconvenient disagreement.
    """
    selection = primary.get('selection')
    R.require(isinstance(selection, dict), 'Primary report has no declared sample')
    if selection.get('schema') == M.SAMPLE_SCHEMA:
        R.require(previous is not None and isinstance(catalog_bytes, bytes),
                  'Corrected primary evidence requires its pinned previous context and catalog')
        R.require(re.fullmatch('[a-f0-9]{40}', primary.get('catalogGitBlob', '')) and
                  R.git_blob(catalog_bytes) == primary['catalogGitBlob'],
                  'Corrected primary catalog bytes differ from its immutable pin')
        catalog = json.loads(catalog_bytes)
        plan, old_plan, old_report, _, _ = M.load_context(
            data_bytes, recipe, policy, catalog, previous, stage_bytes, baseline)
        expected = M.make_selection(data_bytes, stage_bytes, recipe, policy, baseline, old_plan, old_report)
        R.require(selection == expected, 'Corrected primary sample differs from its reproduced selection')
        R.require(primary.get('sourceDataSha256') == plan['sourceDataSha256'],
                  'Corrected primary source differs from the reviewed bytes')
    else:
        plan = F.validate_selection(selection, data_bytes, stage_bytes, recipe, policy, baseline)
    return plan, selection


def validate_reuse(report, contract, profile, keys, primary, plan, state):
    R.require(report.get('schema') == REPORT_SCHEMA and report.get('approvalGranted') is False and
              report.get('primaryReport') == contract and report.get('asrProfile') == profile and
              report.get('selectedDisagreementKeys') == keys, 'Cached crosscheck belongs to different evidence')
    results = report.get('results', {})
    R.require(isinstance(results, dict) and set(results) <= set(keys), 'Unexpected cached crosscheck key')
    for key, result in results.items():
        R.require(result.get('audioKey') == key and result.get('mp3Sha256') == state['clips'][key]['sha256'] and
                  result.get('textSha256') == plan['tasks'][key]['textSha256'] and result.get('primary') == primary['results'][key],
                  'Cached crosscheck audio, text or primary transcript changed')
        secondary = result.get('secondary', {})
        for field, value in A.assess(plan['tasks'][key]['text'], secondary.get('transcript', '')).items():
            R.require(secondary.get(field) == value, 'Cached secondary assessment cannot be reproduced')
    return dict(results)


def publish(client, report, primary_sha):
    R.require(report.get('schema') == REPORT_SCHEMA and report.get('approvalGranted') is False,
              'Only evidence without approval may use the crosscheck branch')
    data = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
    prefix = '/repos/' + R.REPOSITORY
    ref_path = prefix + '/git/ref/heads/' + R.quote(REPORT_BRANCH, safe='/')
    ref = client.request('GET', ref_path, allow404=True)
    parent_sha = ref['object']['sha'] if ref else os.environ.get('GITHUB_SHA', '')
    R.require(re.fullmatch('[a-f0-9]{40}', parent_sha), 'Pinned crosscheck parent required')
    parent = client.request('GET', prefix + '/git/commits/' + parent_sha)
    blob = client.request('POST', prefix + '/git/blobs', {'content': base64.b64encode(data).decode(), 'encoding': 'base64'})
    R.require(blob['sha'] == R.git_blob(data), 'Crosscheck Git checksum mismatch')
    tree = client.request('POST', prefix + '/git/trees', {'base_tree': parent['tree']['sha'], 'tree': [
        {'path': report_path(primary_sha), 'mode': '100644', 'type': 'blob', 'sha': blob['sha']}]})
    if tree['sha'] == parent['tree']['sha']:
        return {'branch': REPORT_BRANCH, 'commit': parent_sha, 'changed': False}
    commit = client.request('POST', prefix + '/git/commits', {'message': 'Record second-model F1 disagreement evidence ' + primary_sha[:16],
                                                           'tree': tree['sha'], 'parents': [parent_sha]})
    if ref:
        R.require(client.request('GET', ref_path)['object']['sha'] == parent_sha, 'Crosscheck branch changed; refusing overwrite')
        client.request('PATCH', prefix + '/git/refs/heads/' + R.quote(REPORT_BRANCH, safe='/'), {'sha': commit['sha'], 'force': False})
    else:
        client.request('POST', prefix + '/git/refs', {'ref': 'refs/heads/' + REPORT_BRANCH, 'sha': commit['sha']})
    return {'branch': REPORT_BRANCH, 'commit': commit['sha'], 'changed': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', default='reader-assistance/data.json')
    parser.add_argument('--stage-data', default='reader-assistance/staged.json')
    parser.add_argument('--recipe', default='reader-assistance/recipe.json')
    parser.add_argument('--baseline', default='reader-assistance/verification-report-d6d40bb76000a71f.json')
    parser.add_argument('--selection', help='Optional exact copy of the primary report sample; never overrides it')
    parser.add_argument('--input', default='reader-assistance/crosscheck-input.json')
    parser.add_argument('--context', default='reader-assistance-review-context')
    parser.add_argument('--catalog', default='reader-assistance/audio-manifest.json')
    parser.add_argument('--model-cache', default='reader-assistance-small-asr-model')
    parser.add_argument('--out', default='reader-assistance-crosscheck-report.json')
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    data_bytes, stage_bytes = Path(args.data).read_bytes(), Path(args.stage_data).read_bytes()
    recipe, baseline = json.loads(Path(args.recipe).read_text()), json.loads(Path(args.baseline).read_text())
    policy = json.loads(S.POLICY_PATH.read_text())
    R.require(os.environ.get('GITHUB_REPOSITORY', R.REPOSITORY) == R.REPOSITORY, 'Unexpected repository')
    client = R.GitHub()
    contract = json.loads(Path(args.input).read_text())
    primary = load_primary(client, contract)
    previous = catalog = catalog_bytes = None
    revision = primary.get('selection', {}).get('schema') == M.SAMPLE_SCHEMA
    if revision:
        context_dir = Path(args.context)
        previous = {name: M.materialize_blob(client, blob, context_dir / name) for name, blob in M.PRIOR.items()}
        catalog_bytes = M.materialize_blob(client, primary.get('catalogGitBlob'), args.catalog)
        catalog = json.loads(catalog_bytes)
    plan, selection = validate_primary_selection(primary, data_bytes, stage_bytes, recipe, policy, baseline,
                                                previous=previous, catalog_bytes=catalog_bytes)
    if args.selection:
        R.require(json.loads(Path(args.selection).read_text()) == selection,
                  'Supplied selection cannot replace the declared primary sample')
    state = R.inventory(client, plan)
    if revision:
        M.validate_live_catalog(plan, catalog, state)
    primary_profile = primary.get('asrProfile', {})
    expected = {'modelRepository': V.MODEL_REPOSITORY, 'modelRevision': V.MODEL_REVISION,
                'device': 'cpu', 'computeType': 'int8', 'cpuThreads': 4, 'beamSize': 5,
                'language': 'ko', 'vadFilter': False, 'conditionOnPreviousText': False,
                'temperature': 0, 'targetPromptUsed': False,
                'judgeGitBlob': R.git_blob((R.ROOT / 'research/judge.py').read_bytes()),
                'koVoiceGitBlob': R.git_blob((R.ROOT / 'public/ko-voice.js').read_bytes()),
                'assessmentGitBlob': R.git_blob((R.ROOT / 'tools/reader_assistance_asr.py').read_bytes()),
                'verificationGitBlob': R.git_blob((R.ROOT / 'tools/reader_assistance_verify.py').read_bytes()),
                'finalVerificationGitBlob': R.git_blob((R.ROOT / 'tools/reader_assistance_final_verify.py').read_bytes()),
                'fasterWhisperVersion': importlib.metadata.version('faster-whisper'),
                'ctranslate2Version': importlib.metadata.version('ctranslate2')}
    if revision:
        expected['revisionVerificationGitBlob'] = R.git_blob((R.ROOT / 'tools/reader_assistance_revision_verify.py').read_bytes())
    R.require(primary_profile == expected, 'Primary model or assessment code changed')
    keys = disagreement_keys(primary, selection, primary_profile, plan, state)
    profile = {**{key: value for key, value in expected.items() if key not in ('verificationGitBlob', 'finalVerificationGitBlob', 'revisionVerificationGitBlob')},
               'modelRepository': MODEL_REPOSITORY, 'modelRevision': MODEL_REVISION,
               'crosscheckGitBlob': R.git_blob(Path(__file__).read_bytes())}
    results = {}
    endpoint = '/repos/' + R.REPOSITORY + '/contents/' + report_path(contract['sha256']) + '?ref=' + R.quote(REPORT_BRANCH, safe='')
    cached = client.request('GET', endpoint, allow404=True)
    if cached:
        R.require(cached.get('encoding') == 'base64', 'Unexpected cached crosscheck encoding')
        cached_bytes = base64.b64decode(cached['content'])
        R.require(R.git_blob(cached_bytes) == cached['sha'], 'Cached crosscheck checksum mismatch')
        results = validate_reuse(json.loads(cached_bytes), contract, profile, keys, primary, plan, state)
    reused, started = len(results), time.perf_counter()
    pending = [key for key in keys if key not in results]
    if pending:
        from huggingface_hub import snapshot_download
        from faster_whisper import WhisperModel
        path = Path(snapshot_download(repo_id=MODEL_REPOSITORY, revision=MODEL_REVISION,
                                      cache_dir=args.model_cache, allow_patterns=list(A.ASR_FILES)))
        R.require(path.name == MODEL_REVISION, 'Secondary ASR revision mismatch')
        A.check_model_snapshot(path)
        model = WhisperModel(str(path), device='cpu', compute_type='int8', cpu_threads=4,
                             num_workers=1, local_files_only=True)
        (Path(args.model_cache) / 'small.ready').write_text(MODEL_REVISION + '\n')
        for number, key in enumerate(pending, 1):
            task, clip = plan['tasks'][key], state['clips'][key]
            mp3 = A.clip_bytes(clip, state['packs'][clip['pack']])
            begin = time.perf_counter()
            segments, _ = model.transcribe(io.BytesIO(mp3), language='ko', task='transcribe', beam_size=5,
                                          temperature=0, vad_filter=False, condition_on_previous_text=False,
                                          initial_prompt=None)
            segments = list(segments)
            secondary = A.assess(task['text'], ''.join(segment.text for segment in segments).strip())
            results[key] = {'audioKey': key, 'mp3Sha256': clip['sha256'], 'textSha256': task['textSha256'],
                            'durationMs': clip['durationMs'], 'primary': primary['results'][key], 'secondary': secondary,
                            'asrSeconds': round(time.perf_counter() - begin, 3),
                            'segments': [{'start': segment.start, 'end': segment.end, 'text': segment.text,
                                          'avgLogProbability': round(segment.avg_logprob, 6)} for segment in segments]}
            print(json.dumps({'assessed': number, 'newAssessments': len(pending), 'audioKey': key,
                              'secondaryStatus': secondary['status'], 'criticalMatch': secondary['criticalMatch']}), flush=True)
    report = {'schema': REPORT_SCHEMA, 'approvalGranted': False, 'directListeningCompleted': False,
              'sourceDataSha256': plan['sourceDataSha256'], 'recipeSha256': plan['recipeSha256'], 'releaseScope': policy,
              'primaryReport': contract, 'primaryAsrProfile': primary_profile, 'asrProfile': profile,
              'selectedDisagreementKeys': keys, 'assessedDisagreements': len(results),
              'allPrimaryDisagreementsCrosschecked': set(results) == set(keys),
              'secondaryCounts': dict(Counter(item['secondary']['status'] for item in results.values())),
              'results': {key: results[key] for key in sorted(results)},
              'timing': {'elapsedSeconds': round(time.perf_counter() - started, 3), 'reusedAssessments': reused},
              'limitation': 'Both transcribers use different pinned Whisper models from the same family. Secondary agreement does not establish correct synthesis or override any primary disagreement. No approval, direct listening, source change or normalization exception was made.'}
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    if args.publish:
        print(json.dumps(publish(client, report, contract['sha256'])), flush=True)
    summary = {'primaryDisagreements': len(keys), 'secondaryCounts': report['secondaryCounts'],
               'allPrimaryDisagreementsCrosschecked': report['allPrimaryDisagreementsCrosschecked'], 'approvalGranted': False}
    print(json.dumps(summary), flush=True)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as output:
            output.write('## Second-model disagreement evidence\n\n' + json.dumps(summary) + '\n\n' + report['limitation'] + '\n')
    if keys:
        raise SystemExit(2)


if __name__ == '__main__':
    try:
        main()
    except (R.ContractError, json.JSONDecodeError, OSError) as error:
        raise SystemExit('reader-assistance-crosscheck: ' + str(error))
