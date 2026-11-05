#!/usr/bin/env python3
"""Fixed final-scope ASR sample; reuse only exact, independently recorded clips.

This wrapper does not change the renderer, ASR model or assessment algorithm. It
adds final-scope selection and safely reuses evidence from the earlier staging
sample. It never grants a text/audio approval or claims direct listening.
"""
from __future__ import annotations

import argparse
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

SAMPLE_SCHEMA = 'reader-assistance-final-verification-sample.v1'
STRATEGY = 'final-base65-amended5-new-boundary12-new-longest4-prior-predeclared.v1'
AMENDED_INDICES = {704, 4489, 4490, 6255, 6756}


def make_selection(data_bytes, stage_bytes, recipe, policy, baseline):
    plan = S.build_subset_plan(data_bytes, recipe, policy)
    stage_plan = V.plan_for(stage_bytes, recipe)
    base = V.make_selection(data_bytes, recipe)
    R.require(baseline.get('schema') == V.REPORT_SCHEMA and baseline.get('approvalGranted') is False and
              baseline.get('sourceDataSha256') == stage_plan['sourceDataSha256'], 'Wrong staged evidence source')
    V.validate_selection(baseline['selection'], stage_bytes, recipe)
    selected = {entry['audioKey']: dict(entry, reasons=list(entry['reasons'])) for entry in base['entries']}
    entries = json.loads(data_bytes)['entries']
    by_key = {}
    for entry, binding in zip(entries, plan['entries']):
        by_key.setdefault(binding['audioKey'], entry)

    def add(keys, reason):
        for key in keys:
            entry = by_key[key]
            value = selected.setdefault(key, {'audioKey': key, 'entryId': entry['id'],
                                              'sourceIndex': entry['sourceIndex'],
                                              'textSha256': plan['tasks'][key]['textSha256'], 'reasons': []})
            if reason not in value['reasons']:
                value['reasons'].append(reason)

    add([binding['audioKey'] for entry, binding in zip(entries, plan['entries'])
         if entry['sourceIndex'] in AMENDED_INDICES], 'amended-reviewed-explanation')
    new = sorted(set(plan['tasks']) - set(stage_plan['tasks']))
    boundary = r'\d+\s*(?:개월|년|일|시간|분|세|명|회|발|원|%)|이상|이하|미만|초과|징역|금고|벌금|과태료|제외|해야|할 수|없습니다'
    add(sorted(new, key=lambda key: (-len(re.findall(boundary, plan['tasks'][key]['text'])), key))[:12],
        'new-reviewed-numeric-or-modal')
    add(sorted(new, key=lambda key: (-len(plan['tasks'][key]['text']), key))[:4], 'new-reviewed-longest')
    # Keep the whole earlier predeclared sample that still belongs to the final
    # scope, including clips that were not ready yet and every prior result.
    add(sorted({entry['audioKey'] for entry in baseline['selection']['entries']} & set(plan['tasks'])),
        'prior-predeclared-staged-clip')
    add(sorted(set(baseline.get('results', {})) & set(plan['tasks'])), 'prior-assessed-staged-clip')
    R.require(len(selected) <= 128, 'Final sample exceeds its predeclared bound')
    return {'schema': SAMPLE_SCHEMA, 'strategy': STRATEGY, 'sourceDataSha256': plan['sourceDataSha256'],
            'stagedSourceDataSha256': stage_plan['sourceDataSha256'], 'recipeSha256': plan['recipeSha256'],
            'corpusApprovedEntries': plan['expectedEntries'], 'releaseScope': policy,
            'priorReportSha256': R.sha256(R.canonical_json(baseline)),
            'selectedUniqueClips': len(selected), 'newReviewedUniqueClips': len(new),
            'entries': [selected[key] for key in sorted(selected)]}


def validate_selection(selection, data_bytes, stage_bytes, recipe, policy, baseline):
    R.require(selection == make_selection(data_bytes, stage_bytes, recipe, policy, baseline),
              'Final sample differs from its predeclared selection; no post-hoc removals are allowed')
    return S.build_subset_plan(data_bytes, recipe, policy)


def reuse_baseline(baseline, selection, profile, stage_plan, plan, state):
    core_profile = {key: value for key, value in profile.items() if key != 'finalVerificationGitBlob'}
    R.require(baseline.get('asrProfile') == core_profile, 'Staged evidence used a different model or assessment algorithm')
    old = V.validate_cached(baseline, baseline['selection'], core_profile, stage_plan, state)
    selected = {entry['audioKey'] for entry in selection['entries']}
    result = {}
    for key in sorted(set(old) & selected):
        task, clip, prior = plan['tasks'][key], state['clips'][key], old[key]
        R.require(prior['target'] == task['text'] and prior['textSha256'] == task['textSha256'] and
                  prior['mp3Sha256'] == clip['sha256'], 'Prior result cannot be rebound to a changed final clip')
        result[key] = {**prior, 'reusedFromReportSha256': selection['priorReportSha256']}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['sample', 'audit'])
    parser.add_argument('--data', default='reader-assistance/data.json')
    parser.add_argument('--stage-data', default='reader-assistance/staged.json')
    parser.add_argument('--recipe', default='reader-assistance/recipe.json')
    parser.add_argument('--baseline', default='reader-assistance/verification-report-d6d40bb76000a71f.json')
    parser.add_argument('--selection', default='reader-assistance/final-verification-sample.json')
    parser.add_argument('--model-cache', default='reader-assistance-asr-model')
    parser.add_argument('--out', default='reader-assistance-final-verification-report.json')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    data_bytes, stage_bytes = Path(args.data).read_bytes(), Path(args.stage_data).read_bytes()
    recipe = json.loads(Path(args.recipe).read_text())
    policy = json.loads(S.POLICY_PATH.read_text())
    baseline = json.loads(Path(args.baseline).read_text())
    if args.command == 'sample':
        selection = make_selection(data_bytes, stage_bytes, recipe, policy, baseline)
        Path(args.selection).write_text(json.dumps(selection, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
        print(json.dumps({'selectedUniqueClips': selection['selectedUniqueClips'],
                          'newReviewedUniqueClips': selection['newReviewedUniqueClips'],
                          'sourceDataSha256': selection['sourceDataSha256']}))
        return
    selection = json.loads(Path(args.selection).read_text())
    plan = validate_selection(selection, data_bytes, stage_bytes, recipe, policy, baseline)
    stage_plan = V.plan_for(stage_bytes, recipe)
    profile = {'modelRepository': V.MODEL_REPOSITORY, 'modelRevision': V.MODEL_REVISION,
               'device': 'cpu', 'computeType': 'int8', 'cpuThreads': 4, 'beamSize': 5,
               'language': 'ko', 'vadFilter': False, 'conditionOnPreviousText': False,
               'temperature': 0, 'targetPromptUsed': False,
               'judgeGitBlob': R.git_blob((R.ROOT / 'research/judge.py').read_bytes()),
               'koVoiceGitBlob': R.git_blob((R.ROOT / 'public/ko-voice.js').read_bytes()),
               'assessmentGitBlob': R.git_blob((R.ROOT / 'tools/reader_assistance_asr.py').read_bytes()),
               'verificationGitBlob': R.git_blob((R.ROOT / 'tools/reader_assistance_verify.py').read_bytes()),
               'finalVerificationGitBlob': R.git_blob(Path(__file__).read_bytes()),
               'fasterWhisperVersion': importlib.metadata.version('faster-whisper'),
               'ctranslate2Version': importlib.metadata.version('ctranslate2')}
    R.require(os.environ.get('GITHUB_REPOSITORY', R.REPOSITORY) == R.REPOSITORY, 'Unexpected repository')
    client = R.GitHub()
    cache = Path(os.environ.get('RUNNER_TEMP', '/tmp')) / 'reader-assistance-pack-indexes'
    state = R.inventory(client, plan, cache_dir=cache)
    results = reuse_baseline(baseline, selection, profile, stage_plan, plan, state)
    if args.resume:
        previous = V.cached_report(client, selection)
        if previous is not None:
            results.update(V.validate_cached(previous, selection, profile, plan, state))
    reused = len(results)
    selected = {entry['audioKey'] for entry in selection['entries']}
    started, model = time.perf_counter(), None
    new_assessments = 0
    # At most two inventory snapshots: new clips that finish while the first
    # batch is being transcribed can join the same run without polling or model
    # reloads. Any still unavailable clips remain an explicit failure.
    for cycle in range(2):
        if cycle:
            state = R.inventory(client, plan, cache_dir=cache)
        pending = sorted(selected.intersection(state['clips']) - set(results))
        if pending and model is None:
            from huggingface_hub import snapshot_download
            from faster_whisper import WhisperModel
            model_path = Path(snapshot_download(repo_id=V.MODEL_REPOSITORY, revision=V.MODEL_REVISION,
                                               cache_dir=args.model_cache, allow_patterns=list(A.ASR_FILES)))
            R.require(model_path.name == V.MODEL_REVISION, 'ASR model revision mismatch')
            A.check_model_snapshot(model_path)
            model = WhisperModel(str(model_path), device='cpu', compute_type='int8', cpu_threads=4,
                                 num_workers=1, local_files_only=True)
            (Path(args.model_cache) / 'medium.ready').write_text(V.MODEL_REVISION + '\n')
        for key in pending:
            task, clip = plan['tasks'][key], state['clips'][key]
            mp3 = A.clip_bytes(clip, state['packs'][clip['pack']])
            begin = time.perf_counter()
            segments, _ = model.transcribe(io.BytesIO(mp3), language='ko', task='transcribe', beam_size=5,
                                          temperature=0, vad_filter=False, condition_on_previous_text=False,
                                          initial_prompt=None)
            segments = list(segments)
            transcript = ''.join(segment.text for segment in segments).strip()
            result = A.assess(task['text'], transcript)
            result.update(audioKey=key, mp3Sha256=clip['sha256'], textSha256=task['textSha256'],
                          durationMs=clip['durationMs'], asrSeconds=round(time.perf_counter() - begin, 3),
                          segments=[{'start': segment.start, 'end': segment.end, 'text': segment.text,
                                     'avgLogProbability': round(segment.avg_logprob, 6)} for segment in segments])
            results[key] = result
            new_assessments += 1
            print(json.dumps({'newAssessments': new_assessments, 'audioKey': key, 'status': result['status'],
                              'criticalMatch': result['criticalMatch']}), flush=True)
        if selected <= set(results):
            break
    report = V.evidence_report(selection, profile, plan, state, results, time.perf_counter() - started, reused)
    report['releaseScope'] = policy
    report['baselineEvidence'] = {'sourceDataSha256': stage_plan['sourceDataSha256'],
                                  'reportSha256': selection['priorReportSha256'],
                                  'assessedUniqueClips': len(baseline['results']),
                                  'omittedChangedOrOutOfScopeKeys': sorted({entry['audioKey'] for entry in baseline['selection']['entries']} - set(plan['tasks']))}
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    if args.publish:
        print(json.dumps(V.publish_report(client, report)), flush=True)
    summary = {'counts': report['counts'], 'coverage': report['coverage'], 'reusedAssessments': reused}
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as output:
            output.write('## Final reviewed-scope ASR evidence\n\n' + json.dumps(summary, ensure_ascii=False) + '\n\n')
            output.write('No direct listening or approval is claimed. All disagreements and missing samples remain explicit.\n')
    print(json.dumps(summary), flush=True)
    if report['counts'].get('review_required') or not report['coverage']['allSelectedClipsAssessed']:
        raise SystemExit(2)


if __name__ == '__main__':
    try:
        main()
    except (R.ContractError, json.JSONDecodeError, OSError) as error:
        raise SystemExit('reader-assistance-final-verify: ' + str(error))
