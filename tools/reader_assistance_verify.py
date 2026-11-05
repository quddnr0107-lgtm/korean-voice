#!/usr/bin/env python3
"""Predeclared, independent ASR evidence for exact approved F1 corpus clips.

This tool never generates speech or approves text/audio. The same pinned medium
model used by the pilot checks a deterministic sample without a target prompt.
Incomplete coverage and transcription disagreements remain explicit in reports.
"""
from __future__ import annotations

import argparse
from collections import Counter
import base64
import importlib.metadata
import io
import json
import os
from pathlib import Path
import re
import time

import reader_assistance as R
import reader_assistance_asr as A

MODEL_REPOSITORY = 'Systran/faster-whisper-medium'
MODEL_REVISION = '08e178d48790749d25932bbc082711ddcfdfbc4f'
REPORT_BRANCH = 'data/reader-assistance-f1-verification'
SAMPLE_SCHEMA = 'reader-assistance-verification-sample.v1'
REPORT_SCHEMA = 'reader-assistance-verification-report.v1'
STRATEGY = 'hash-prefix-2-longest-6-boundary-10-penalty-8-exception-4-rare-8.v1'


def plan_for(data_bytes, recipe):
    R.check_recipe(recipe, verify_sources=True)
    return R.build_plan(data_bytes, recipe, allow_partial=True)


def make_selection(data_bytes, recipe):
    plan = plan_for(data_bytes, recipe)
    data = json.loads(data_bytes)
    entries = {entry['id']: entry for entry in data['entries']}
    by_key = {}
    for binding in plan['entries']:
        by_key.setdefault(binding['audioKey'], entries[binding['id']])
    selected = {}

    def add(keys, reason):
        for key in keys:
            item = selected.setdefault(key, {'audioKey': key, 'entryId': by_key[key]['id'],
                                           'sourceIndex': by_key[key].get('sourceIndex'),
                                           'textSha256': plan['tasks'][key]['textSha256'], 'reasons': []})
            if reason not in item['reasons']:
                item['reasons'].append(reason)

    tasks = plan['tasks']
    # These keys occur in each bucket's first pack, enabling an early check while
    # the remainder is still baking. Hash order is independent of ASR results.
    for bucket in '0123456789abcdef':
        add([key for key in tasks if key[0] == bucket][:2], 'first-pack-hash-sample')
    add(sorted(tasks, key=lambda key: (-len(tasks[key]['text']), key))[:6], 'longest-text')

    def ranked(pattern, count, reason):
        scored = [(len(re.findall(pattern, task['text'])), key) for key, task in tasks.items()]
        keys = [key for score, key in sorted(scored, key=lambda item: (-item[0], item[1])) if score]
        add(keys[:count], reason)

    ranked(r'\d+\s*(?:개월|년|일|시간|분|세|명|회|발|원|%)|이상|이하|미만|초과', 10, 'numeric-boundary')
    ranked(r'징역|금고|벌금|과태료|면제|해제|해야|하여야|할 수|하지 않|없습니다', 8, 'penalty-and-modality')
    ranked(r'다만|제외|예외|하지만', 4, 'exception')
    ranked(r'쉼터|입영판정검사|아동보호치료시설', 8, 'pilot-lexical-risk')
    return {'schema': SAMPLE_SCHEMA, 'strategy': STRATEGY,
            'sourceDataSha256': plan['sourceDataSha256'], 'recipeSha256': plan['recipeSha256'],
            'corpusApprovedEntries': plan['expectedEntries'], 'selectedUniqueClips': len(selected),
            'entries': [selected[key] for key in sorted(selected)]}


def validate_selection(selection, data_bytes, recipe):
    expected = make_selection(data_bytes, recipe)
    R.require(selection == expected, 'Verification sample differs from the predeclared deterministic selection')
    R.require(0 < selection['selectedUniqueClips'] <= 96, 'Verification sample size outside the declared bound')
    return plan_for(data_bytes, recipe)


def report_path(selection):
    return 'reader-assistance/verification-report-' + selection['sourceDataSha256'][:16] + '.json'


def cached_report(client, selection):
    path = report_path(selection)
    endpoint = '/repos/' + R.REPOSITORY + '/contents/' + path + '?ref=' + R.quote(REPORT_BRANCH, safe='')
    payload = client.request('GET', endpoint, allow404=True)
    if payload is None:
        return None
    R.require(payload.get('encoding') == 'base64', 'Unexpected verification report encoding')
    content = base64.b64decode(payload['content'])
    R.require(R.git_blob(content) == payload['sha'], 'Cached verification report checksum mismatch')
    return json.loads(content)


def validate_cached(report, selection, profile, plan, state):
    R.require(report.get('schema') == REPORT_SCHEMA and report.get('selection') == selection and
              report.get('asrProfile') == profile and report.get('approvalGranted') is False,
              'Cached verification evidence belongs to a different source, selection or ASR profile')
    selected = {entry['audioKey'] for entry in selection['entries']}
    results = report.get('results', {})
    R.require(isinstance(results, dict) and set(results) <= selected, 'Unexpected cached verification keys')
    for key, result in results.items():
        clip, task = state['clips'].get(key), plan['tasks'][key]
        R.require(clip and result.get('audioKey') == key and result.get('mp3Sha256') == clip['sha256'] and
                  result.get('textSha256') == task['textSha256'] and result.get('target') == task['text'],
                  'Cached verification audio or text identity changed')
        R.require(result.get('status') in ('normalized_match', 'review_required'), 'Unexpected cached assessment status')
        for field, value in A.assess(task['text'], result.get('transcript', '')).items():
            R.require(result.get(field) == value, 'Cached assessment cannot be reproduced from its transcript')
    return dict(results)


def evidence_report(selection, profile, plan, state, results, elapsed, reused):
    selected = {entry['audioKey'] for entry in selection['entries']}
    R.require(set(results) <= selected, 'Assessment exceeds its predeclared sample')
    missing_audio = sorted(selected - set(state['clips']))
    unassessed = sorted(selected - set(results))
    counts = dict(Counter(result['status'] for result in results.values()))
    return {'schema': REPORT_SCHEMA, 'approvalGranted': False, 'directListeningCompleted': False,
            'sourceDataSha256': plan['sourceDataSha256'], 'recipeSha256': plan['recipeSha256'],
            'selection': selection, 'asrProfile': profile,
            'coverage': {'allCorpusAssessed': False, 'selectedUniqueClips': len(selected),
                         'assessedUniqueClips': len(results), 'allSelectedClipsAssessed': not unassessed,
                         'missingAudioKeys': missing_audio, 'unassessedAudioKeys': unassessed},
            'counts': counts, 'results': {key: results[key] for key in sorted(results)},
            'timing': {'elapsedSeconds': round(elapsed, 3), 'reusedAssessments': reused,
                       'audioSeconds': round(sum(v['durationMs'] for v in results.values()) / 1000, 3),
                       'asrSeconds': round(sum(v['asrSeconds'] for v in results.values()), 3)},
            'limitation': 'Independent automated transcription of a predeclared sample is evidence, not direct listening, legal review, or permission to activate. All disagreements remain review_required.'}


def publish_report(client, report):
    R.require(report.get('schema') == REPORT_SCHEMA and report.get('approvalGranted') is False,
              'Only non-approving verification evidence can be published here')
    data = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
    prefix = '/repos/' + R.REPOSITORY
    ref_path = prefix + '/git/ref/heads/' + R.quote(REPORT_BRANCH, safe='/')
    ref = client.request('GET', ref_path, allow404=True)
    parent_sha = ref['object']['sha'] if ref else os.environ.get('GITHUB_SHA', '')
    R.require(re.fullmatch('[a-f0-9]{40}', parent_sha) is not None, 'Pinned report parent required')
    parent = client.request('GET', prefix + '/git/commits/' + parent_sha)
    blob = client.request('POST', prefix + '/git/blobs', {'content': base64.b64encode(data).decode(), 'encoding': 'base64'})
    R.require(blob['sha'] == R.git_blob(data), 'Verification report Git checksum mismatch')
    tree = client.request('POST', prefix + '/git/trees', {'base_tree': parent['tree']['sha'], 'tree': [
        {'path': report_path(report['selection']), 'mode': '100644', 'type': 'blob', 'sha': blob['sha']}]})
    if tree['sha'] == parent['tree']['sha']:
        return {'branch': REPORT_BRANCH, 'commit': parent_sha, 'changed': False}
    commit = client.request('POST', prefix + '/git/commits', {'message': 'Record fixed-sample F1 transcription evidence ' + report['sourceDataSha256'][:16],
                                                           'tree': tree['sha'], 'parents': [parent_sha]})
    if ref:
        current = client.request('GET', ref_path)
        R.require(current['object']['sha'] == parent_sha, 'Evidence branch changed; refusing overwrite')
        client.request('PATCH', prefix + '/git/refs/heads/' + R.quote(REPORT_BRANCH, safe='/'), {'sha': commit['sha'], 'force': False})
    else:
        client.request('POST', prefix + '/git/refs', {'ref': 'refs/heads/' + REPORT_BRANCH, 'sha': commit['sha']})
    return {'branch': REPORT_BRANCH, 'commit': commit['sha'], 'changed': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['sample', 'audit'])
    parser.add_argument('--data', default='reader-assistance/staged.json')
    parser.add_argument('--recipe', default='reader-assistance/recipe.json')
    parser.add_argument('--selection', default='reader-assistance/verification-sample.json')
    parser.add_argument('--model-cache', default='reader-assistance-asr-model')
    parser.add_argument('--out', default='reader-assistance-verification-report.json')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--require-all', action='store_true', help='Fail if any predeclared sample audio remains unavailable')
    args = parser.parse_args()
    data_bytes = Path(args.data).read_bytes()
    recipe = json.loads(Path(args.recipe).read_text())
    if args.command == 'sample':
        selection = make_selection(data_bytes, recipe)
        Path(args.selection).parent.mkdir(parents=True, exist_ok=True)
        Path(args.selection).write_text(json.dumps(selection, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
        print(json.dumps({'selectedUniqueClips': selection['selectedUniqueClips'], 'sourceDataSha256': selection['sourceDataSha256']}))
        return
    selection = json.loads(Path(args.selection).read_text())
    plan = validate_selection(selection, data_bytes, recipe)
    profile = {'modelRepository': MODEL_REPOSITORY, 'modelRevision': MODEL_REVISION,
               'device': 'cpu', 'computeType': 'int8', 'cpuThreads': 4, 'beamSize': 5,
               'language': 'ko', 'vadFilter': False, 'conditionOnPreviousText': False,
               'temperature': 0, 'targetPromptUsed': False,
               'judgeGitBlob': R.git_blob((R.ROOT / 'research/judge.py').read_bytes()),
               'koVoiceGitBlob': R.git_blob((R.ROOT / 'public/ko-voice.js').read_bytes()),
               'assessmentGitBlob': R.git_blob((R.ROOT / 'tools/reader_assistance_asr.py').read_bytes()),
               'verificationGitBlob': R.git_blob(Path(__file__).read_bytes()),
               'fasterWhisperVersion': importlib.metadata.version('faster-whisper'),
               'ctranslate2Version': importlib.metadata.version('ctranslate2')}
    R.require(os.environ.get('GITHUB_REPOSITORY', R.REPOSITORY) == R.REPOSITORY, 'Unexpected repository')
    client = R.GitHub()
    state = R.inventory(client, plan)
    results = {}
    if args.resume:
        previous = cached_report(client, selection)
        if previous is not None:
            results = validate_cached(previous, selection, profile, plan, state)
    reused = len(results)
    pending = [entry['audioKey'] for entry in selection['entries']
               if entry['audioKey'] in state['clips'] and entry['audioKey'] not in results]
    R.require(pending or results, 'None of the predeclared sample audio is available yet')
    started = time.perf_counter()
    if pending:
        from huggingface_hub import snapshot_download
        from faster_whisper import WhisperModel
        model_path = Path(snapshot_download(repo_id=MODEL_REPOSITORY, revision=MODEL_REVISION,
                                           cache_dir=args.model_cache, allow_patterns=list(A.ASR_FILES)))
        R.require(model_path.name == MODEL_REVISION, 'ASR model revision mismatch')
        A.check_model_snapshot(model_path)
        model = WhisperModel(str(model_path), device='cpu', compute_type='int8', cpu_threads=4,
                             num_workers=1, local_files_only=True)
        (Path(args.model_cache) / 'medium.ready').write_text(MODEL_REVISION + '\n')
        for number, key in enumerate(pending, 1):
            task, clip = plan['tasks'][key], state['clips'][key]
            mp3 = A.clip_bytes(clip, state['packs'][clip['pack']])
            begin = time.perf_counter()
            segments, info = model.transcribe(io.BytesIO(mp3), language='ko', task='transcribe',
                                             beam_size=5, temperature=0, vad_filter=False,
                                             condition_on_previous_text=False, initial_prompt=None)
            segments = list(segments)
            transcript = ''.join(segment.text for segment in segments).strip()
            result = A.assess(task['text'], transcript)
            result.update(audioKey=key, mp3Sha256=clip['sha256'], textSha256=task['textSha256'],
                          durationMs=clip['durationMs'], asrSeconds=round(time.perf_counter() - begin, 3),
                          segments=[{'start': segment.start, 'end': segment.end, 'text': segment.text,
                                     'avgLogProbability': round(segment.avg_logprob, 6)} for segment in segments])
            results[key] = result
            print(json.dumps({'assessed': number, 'newAssessments': len(pending), 'audioKey': key,
                              'status': result['status'], 'criticalMatch': result['criticalMatch']}), flush=True)
    report = evidence_report(selection, profile, plan, state, results, time.perf_counter() - started, reused)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    if args.publish:
        print(json.dumps(publish_report(client, report)), flush=True)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as output:
            output.write('## Independent fixed-sample transcription evidence\n\n')
            output.write(json.dumps({'coverage': report['coverage'], 'counts': report['counts'], 'reused': reused}, ensure_ascii=False) + '\n\n')
            output.write('No source or audio approval was granted. A transcription disagreement is not proof that either ASR or synthesis is correct.\n')
    print(json.dumps({'counts': report['counts'], 'coverage': report['coverage']}), flush=True)
    if report['counts'].get('review_required') or (args.require_all and not report['coverage']['allSelectedClipsAssessed']):
        raise SystemExit(2)


if __name__ == '__main__':
    try:
        main()
    except (R.ContractError, json.JSONDecodeError, OSError) as error:
        raise SystemExit('reader-assistance-verify: ' + str(error))
