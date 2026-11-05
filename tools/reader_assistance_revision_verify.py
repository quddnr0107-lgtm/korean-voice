#!/usr/bin/env python3
"""Verify a corrected F1 corpus while retaining exact prior evidence and its failures.

No synthesis, approval, production deployment, or change to the frozen renderer.
The previous source, catalog, policy, and evidence are immutable Git blobs. Only
an unchanged text/recipe/audio identity can reuse a previous transcription.
"""
from __future__ import annotations

import argparse
import base64
import importlib.metadata
import io
import json
import os
from pathlib import Path
import time

import reader_assistance as R
import reader_assistance_asr as A
import reader_assistance_final_verify as F
import reader_assistance_subset as S
import reader_assistance_verify as V

PRIOR = {
    'data.json': 'c46ea3bde1436457c8f218889ded7c782e38d685',
    'audio-manifest.json': '4c83bba903bbe3bf1c964fadb39be385743e0d2b',
    'policy.json': '50ba921120928275c6fa20e0c9dab3c5bf6d2754',
    'report.json': '50ac806a0472f41573a1abba99b2d2287df47cea',
}
PRIOR_SOURCE_SHA = '39df5c0dabc5a2c96b1b8a9d6bb9d485c51ed3f8078d8287a9667258466596f7'
SEALED_RECIPE_SHA = '325696fa16298826933979aa72f34cebf1aca3c1e473bb5035cd10265097f0b8'
PINNED_FINAL_VERIFIER = '6e92b23fae0b8da76b3c1a4787645fd8a550d076'
SAMPLE_SCHEMA = 'reader-assistance-revision-verification-sample.v1'
STRATEGY = 'preserve-final-sample-plus-all-new-text-keys.v1'


def json_bytes(path):
    return json.loads(Path(path).read_bytes())


def materialize_blob(client, blob, path):
    R.require(isinstance(blob, str) and R.re.fullmatch('[a-f0-9]{40}', blob), 'An exact Git blob is required')
    path = Path(path)
    if path.is_file() and R.git_blob(path.read_bytes()) == blob:
        return path.read_bytes()
    result = client.request('GET', '/repos/' + R.REPOSITORY + '/git/blobs/' + blob)
    R.require(result.get('sha') == blob and result.get('encoding') == 'base64', 'Unexpected Git blob response')
    content = base64.b64decode(result['content'], validate=False)
    R.require(len(content) <= 32 * 1024 * 1024 and R.git_blob(content) == blob, 'Pinned Git blob checksum mismatch')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return content


def profile_for(baseline, *, revision=False):
    # These are the same model, decoder settings and assessment sources as the
    # prior report, not a new normalizer fitted to the disputed transcripts.
    expected = dict(baseline['asrProfile'])
    paths = {'judgeGitBlob': 'research/judge.py', 'koVoiceGitBlob': 'public/ko-voice.js',
             'assessmentGitBlob': 'tools/reader_assistance_asr.py',
             'verificationGitBlob': 'tools/reader_assistance_verify.py'}
    for key, path in paths.items():
        R.require(expected.get(key) == R.git_blob((R.ROOT / path).read_bytes()), 'Assessment source changed: ' + path)
    R.require(expected.get('modelRepository') == V.MODEL_REPOSITORY and expected.get('modelRevision') == V.MODEL_REVISION
              and expected.get('targetPromptUsed') is False, 'Unexpected pinned ASR model or target prompt')
    R.require(R.git_blob((R.ROOT / 'tools/reader_assistance_final_verify.py').read_bytes()) == PINNED_FINAL_VERIFIER,
              'Prior final-selection verifier changed')
    expected['finalVerificationGitBlob'] = PINNED_FINAL_VERIFIER
    if revision:
        expected['revisionVerificationGitBlob'] = R.git_blob(Path(__file__).read_bytes())
    return expected


def load_context(data_bytes, recipe, policy, catalog, previous, stage_bytes, baseline):
    for name, blob in PRIOR.items():
        R.require(R.git_blob(previous[name]) == blob, 'Previous evidence was not the pinned blob: ' + name)
    old_data, old_catalog = previous['data.json'], json.loads(previous['audio-manifest.json'])
    old_policy, old_report = json.loads(previous['policy.json']), json.loads(previous['report.json'])
    R.require(R.sha256(old_data) == PRIOR_SOURCE_SHA, 'Wrong previous canonical source')
    old_plan = S.build_subset_plan(old_data, recipe, old_policy)
    S.validate_subset_catalog(old_catalog, old_plan, old_policy)
    F.validate_selection(old_report['selection'], old_data, stage_bytes, recipe, old_policy, baseline)
    R.require(old_report.get('directListeningCompleted') is False, 'Previous automatic report cannot claim direct listening')
    old_results = V.validate_cached(old_report, old_report['selection'], profile_for(baseline), old_plan, old_catalog)
    plan = S.build_subset_plan(data_bytes, recipe, policy)
    S.validate_subset_catalog(catalog, plan, policy)
    R.require(plan['recipeSha256'] == SEALED_RECIPE_SHA, 'The selected F1 recipe changed')
    return plan, old_plan, old_report, old_catalog, old_results


def make_selection(data_bytes, stage_bytes, recipe, policy, baseline, old_plan, old_report):
    plan = S.build_subset_plan(data_bytes, recipe, policy)
    base = F.make_selection(data_bytes, stage_bytes, recipe, policy, baseline)
    selected = {entry['audioKey']: dict(entry, reasons=list(entry['reasons'])) for entry in base['entries']}
    source_entries = json.loads(data_bytes)['entries']
    by_key = {}
    for entry, binding in zip(source_entries, plan['entries']):
        by_key.setdefault(binding['audioKey'], entry)

    def add(keys, reason):
        for key in keys:
            entry = by_key[key]
            value = selected.setdefault(key, {'audioKey': key, 'entryId': entry['id'], 'sourceIndex': entry['sourceIndex'],
                                              'textSha256': plan['tasks'][key]['textSha256'], 'reasons': []})
            if reason not in value['reasons']:
                value['reasons'].append(reason)

    prior_selected = {entry['audioKey'] for entry in old_report['selection']['entries']}
    add(sorted(prior_selected & set(plan['tasks'])), 'previous-predeclared-sample-retained')
    new_keys = sorted(set(plan['tasks']) - set(old_plan['tasks']))
    add(new_keys, 'every-corrected-or-new-text-key')
    R.require(len(new_keys) <= 64 and len(selected) <= 160, 'Revision exceeds its bounded incremental audit scope')
    return {'schema': SAMPLE_SCHEMA, 'strategy': STRATEGY, 'sourceDataSha256': plan['sourceDataSha256'],
            'recipeSha256': plan['recipeSha256'], 'priorSourceDataSha256': PRIOR_SOURCE_SHA,
            'priorReportGitBlob': PRIOR['report.json'], 'corpusApprovedEntries': plan['expectedEntries'],
            'selectedUniqueClips': len(selected), 'newUniqueClips': len(new_keys),
            'removedPriorSampleKeys': sorted(prior_selected - set(plan['tasks'])),
            'entries': [selected[key] for key in sorted(selected)]}


def migrate_results(old_results, old_catalog, plan, catalog, selection):
    selected = {entry['audioKey'] for entry in selection['entries']}
    result = {}
    for key in sorted(set(old_results) & selected):
        task, clip, prior = plan['tasks'][key], catalog['clips'][key], old_results[key]
        R.require(prior['target'] == task['text'] and prior['textSha256'] == task['textSha256'],
                  'Previous text evidence cannot be rebound to a changed explanation')
        R.require(prior['mp3Sha256'] == old_catalog['clips'][key]['sha256'] == clip['sha256'],
                  'An immutable audio identity changed; cached transcription cannot be reused')
        result[key] = {**prior, 'reusedFromReportGitBlob': PRIOR['report.json']}
    return result


def validate_live_catalog(plan, catalog, state):
    missing = set(plan['tasks']) - set(state['clips'])
    R.require(not missing, 'Current release is missing audio keys: ' + str(len(missing)))
    for key, pack in catalog['packs'].items():
        R.require(state['packs'].get(key) == pack, 'Published pack metadata changed: ' + key)
    for key, clip in catalog['clips'].items():
        actual = state['clips'][key]
        for field in ('sha256', 'textSha256', 'length', 'durationMs'):
            R.require(actual[field] == clip[field], 'Current public clip identity changed: ' + key + '/' + field)
    return {'allEntryBindingsChecked': True, 'allRequiredKeysPresent': True, 'allReferencedPacksPresent': True,
            'publishedEntries': plan['expectedEntries'], 'uniqueClips': len(plan['tasks']),
            'packs': len(catalog['packs']), 'freshPackIndexes': True,
            'allAudioBytesDownloaded': False, 'allPronunciationsAssessed': False}


def decode_metrics(mp3, clip, text):
    import av
    import numpy as np
    parts = []
    with av.open(io.BytesIO(mp3)) as container:
        R.require(len(container.streams.audio) == 1, 'Expected exactly one MP3 audio stream')
        stream = container.streams.audio[0]
        R.require(stream.codec_context.sample_rate == 44100 and stream.codec_context.channels == 1,
                  'Published MP3 encoding differs from the sealed mono 44.1 kHz recipe')
        for frame in container.decode(stream):
            samples = frame.to_ndarray().reshape(-1)
            if np.issubdtype(samples.dtype, np.integer):
                samples = samples.astype(np.float32) / float(max(abs(np.iinfo(samples.dtype).min), np.iinfo(samples.dtype).max))
            parts.append(samples.astype(np.float32))
    R.require(bool(parts), 'Published MP3 did not decode any samples')
    metrics = R.validate_wave(np.concatenate(parts), 44100, text, decoded=True)
    R.require(abs(metrics['durationMs'] - clip['durationMs']) <= 100, 'Published MP3 duration changed after decoding')
    return metrics


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'audit', 'export'])
    parser.add_argument('--data', default='reader-assistance/data.json')
    parser.add_argument('--source-sha256', required=True)
    parser.add_argument('--catalog-blob', required=True)
    parser.add_argument('--context', default='reader-assistance-review-context')
    parser.add_argument('--catalog', default='reader-assistance/audio-manifest.json')
    parser.add_argument('--model-cache', default='reader-assistance-asr-model')
    parser.add_argument('--out', default='reader-assistance-revision-verification-report.json')
    parser.add_argument('--export-directory', default='reader-assistance-review-clips')
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    R.require(not args.publish or args.command == 'audit', 'Only the evidence audit can publish a report')
    R.require(R.HEX64.fullmatch(args.source_sha256 or ''), 'An exact canonical source SHA-256 is required')
    data_bytes = Path(args.data).read_bytes()
    R.require(R.sha256(data_bytes) == args.source_sha256, 'Current canonical source differs from the reviewed pin')
    recipe, policy = json_bytes('reader-assistance/recipe.json'), json_bytes(S.POLICY_PATH)
    stage_bytes = Path('reader-assistance/staged.json').read_bytes()
    baseline = json_bytes('reader-assistance/verification-report-d6d40bb76000a71f.json')
    client = R.GitHub()
    context_dir = Path(args.context)
    previous = {name: materialize_blob(client, blob, context_dir / name) for name, blob in PRIOR.items()}
    catalog_bytes = materialize_blob(client, args.catalog_blob, args.catalog)
    catalog = json.loads(catalog_bytes)
    plan, old_plan, old_report, old_catalog, old_results = load_context(
        data_bytes, recipe, policy, catalog, previous, stage_bytes, baseline)
    selection = make_selection(data_bytes, stage_bytes, recipe, policy, baseline, old_plan, old_report)
    profile = profile_for(baseline, revision=True)
    migrated = migrate_results(old_results, old_catalog, plan, catalog, selection)
    write_json(context_dir / 'selection.json', selection)
    summary = {'sourceDataSha256': plan['sourceDataSha256'], 'catalogGitBlob': args.catalog_blob,
               'entries': plan['expectedEntries'], 'uniqueClips': len(plan['tasks']),
               'selectedUniqueClips': selection['selectedUniqueClips'], 'newUniqueClips': selection['newUniqueClips'],
               'reusedAssessments': len(migrated), 'pendingAssessments': selection['selectedUniqueClips'] - len(migrated)}
    if args.command == 'prepare':
        print(json.dumps(summary), flush=True)
        return
    for package, field in [('faster-whisper', 'fasterWhisperVersion'), ('ctranslate2', 'ctranslate2Version')]:
        R.require(importlib.metadata.version(package) == profile[field], 'ASR package version changed: ' + package)
    if args.command == 'audit':
        # No disposable index cache: every public pack footer/index is fetched in
        # this run, then each entry is checked against its exact reviewed binding.
        state = R.inventory(client, plan, cache_dir=None)
        media_gate = validate_live_catalog(plan, catalog, state)
        results = dict(migrated)
        cached = V.cached_report(client, selection)
        if cached is not None:
            results.update(V.validate_cached(cached, selection, profile, plan, catalog))
        reused = len(results)
        pending = sorted({entry['audioKey'] for entry in selection['entries']} - set(results))
        started = time.perf_counter()
        if pending:
            from huggingface_hub import snapshot_download
            from faster_whisper import WhisperModel
            model_path = Path(snapshot_download(repo_id=V.MODEL_REPOSITORY, revision=V.MODEL_REVISION,
                                               cache_dir=args.model_cache, allow_patterns=list(A.ASR_FILES)))
            R.require(model_path.name == V.MODEL_REVISION, 'ASR model revision mismatch')
            A.check_model_snapshot(model_path)
            model = WhisperModel(str(model_path), device='cpu', compute_type='int8', cpu_threads=4,
                                 num_workers=1, local_files_only=True)
            (Path(args.model_cache) / 'medium.ready').write_text(V.MODEL_REVISION + '\n')
            for index, key in enumerate(pending, 1):
                task, clip = plan['tasks'][key], catalog['clips'][key]
                mp3 = A.clip_bytes(clip, catalog['packs'][clip['pack']])
                decode_metrics(mp3, clip, task['text'])
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
                print(json.dumps({'assessment': index, 'pending': len(pending), 'audioKey': key,
                                  'status': result['status'], 'criticalMatch': result['criticalMatch']}), flush=True)
        report = V.evidence_report(selection, profile, plan, catalog, results, time.perf_counter() - started, reused)
        report.update(releaseScope=policy, catalogGitBlob=args.catalog_blob, mediaGate=media_gate,
                      priorEvidence={'sourceDataSha256': PRIOR_SOURCE_SHA, 'reportGitBlob': PRIOR['report.json'],
                                     'migratedAssessments': len(migrated),
                                     'removedPriorSampleKeys': selection['removedPriorSampleKeys']})
        write_json(args.out, report)
        if args.publish:
            print(json.dumps(V.publish_report(client, report)), flush=True)
        print(json.dumps({**summary, 'counts': report['counts'], 'coverage': report['coverage']}), flush=True)
        if report['counts'].get('review_required') or not report['coverage']['allSelectedClipsAssessed']:
            raise SystemExit(2)
        return
    report = json_bytes(args.out)
    R.require(report.get('catalogGitBlob') == args.catalog_blob and report.get('directListeningCompleted') is False,
              'Review export must use the current exact non-approving report')
    results = V.validate_cached(report, selection, profile, plan, catalog)
    directory = Path(args.export_directory)
    directory.mkdir(parents=True, exist_ok=True)
    clips = []
    for item in selection['entries']:
        key = item['audioKey']
        clip, task = catalog['clips'][key], plan['tasks'][key]
        mp3 = A.clip_bytes(clip, catalog['packs'][clip['pack']])
        metrics = decode_metrics(mp3, clip, task['text'])
        (directory / (key + '.mp3')).write_bytes(mp3)
        result = results.get(key)
        clips.append({**item, 'file': key + '.mp3', 'target': task['text'], 'mp3Sha256': clip['sha256'],
                      'durationMs': clip['durationMs'], 'decodeMetrics': metrics,
                      'transcript': result.get('transcript') if result else None,
                      'status': result.get('status') if result else 'not_assessed',
                      'criticalMatch': result.get('criticalMatch') if result else None,
                      'directListeningCompleted': False})
    write_json(directory / 'comparison.json', {'sourceDataSha256': plan['sourceDataSha256'],
                'catalogGitBlob': args.catalog_blob, 'selection': selection, 'clips': clips,
                'approvalGranted': False, 'directListeningCompleted': False})
    write_json(directory / 'verification-report.json', report)
    print(json.dumps({'exportedExactMp3s': len(clips), 'directory': str(directory),
                      'directListeningCompleted': False}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except (R.ContractError, json.JSONDecodeError, OSError) as error:
        raise SystemExit('reader-assistance-revision-verify: ' + str(error))
