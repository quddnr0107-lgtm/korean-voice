#!/usr/bin/env python3
"""Independent CPU ASR evidence for exact published F1 clips; never regenerates speech.

Reuses research/judge.py normalization/CER, omitting its costly pYIN prosody analysis.
An ASR disagreement is a review requirement, not proof that either TTS or ASR is right.
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
import statistics
import sys
import time
from urllib.parse import quote

import reader_assistance as R

ROOT = Path(__file__).resolve().parents[1]
JUDGE = R.import_path('reader_assistance_original_judge', ROOT / 'research/judge.py')
REPORT_BRANCH = 'data/reader-assistance-f1-asr-pilot'
PILOT_SOURCE_SHA = '9e386157fec6450c85d999679a1207ebca4b8e702a8a5acbbc2d240c0ba31205'
ASR_MODEL = 'Systran/faster-whisper-small'
NUMBERS = {'영': 0, '공': 0, '일': 1, '이': 2, '삼': 3, '사': 4, '오': 5, '육': 6, '칠': 7, '팔': 8, '구': 9}
UNITS = {'십': 10, '백': 100, '천': 1000, '만': 10000, '억': 100000000, '조': 1000000000000}
NATIVE = {'한': '일', '하나': '일', '두': '이', '둘': '이', '세': '삼', '셋': '삼', '네': '사', '넷': '사',
          '다섯': '오', '여섯': '육', '일곱': '칠', '여덟': '팔', '아홉': '구', '열': '십',
          '열한': '십일', '열두': '십이', '열세': '십삼', '열네': '십사', '열다섯': '십오',
          '열여섯': '십육', '열일곱': '십칠', '열여덟': '십팔', '열아홉': '십구', '스무': '이십', '스물': '이십'}
QUANTITY_UNIT = r'개월|퍼센트|시간|분|초|년|월|일|주|세|명|회|배|원|조|항|호|목|해|달'
QUANTITY = re.compile(r'([영공일이삼사오육칠팔구십백천만억조]+)(' + QUANTITY_UNIT + r')')
CRITICAL = re.compile(r'이상|이하|미만|초과|제외|수있|하여야|해야|아야|어야|여야|하지않|아니|않|못|없')


def sino_value(word):
    if all(char in NUMBERS for char in word):
        return str(int(''.join(str(NUMBERS[char]) for char in word)))
    total = section = number = 0
    for char in word:
        if char in NUMBERS:
            number = NUMBERS[char]
        elif char in UNITS:
            multiplier = UNITS[char]
            if multiplier < 10000:
                section += (number or 1) * multiplier
            else:
                total += (section + number or 1) * multiplier
                section = 0
            number = 0
        else:
            return word
    return str(total + section + number)


def comparable(text):
    """Only script/spacing and unambiguous number-reading forms are made equivalent."""
    result = re.sub(r'[^가-힣0-9a-zA-Z]', '', JUDGE.canonicalize_text(text)).lower()
    result = result.replace('유월', '육월').replace('시월', '십월')
    native = re.compile('(' + '|'.join(sorted(NATIVE, key=len, reverse=True)) + ')(' + QUANTITY_UNIT + ')')
    result = native.sub(lambda match: NATIVE[match[1]] + {'해': '년', '달': '개월'}.get(match[2], match[2]), result)
    # Compare numeric values instead of assuming Whisper writes digits or Korean number words.
    return QUANTITY.sub(lambda match: sino_value(match[1]) + {'해': '년', '달': '개월'}.get(match[2], match[2]), result)


def critical_tokens(canonical):
    items = []
    for match in re.finditer(r'\d+(?:개월|퍼센트|시간|분|초|년|월|일|주|세|명|회|배|원|조|항|호|목)', canonical):
        items.append((match.start(), 'quantity:' + match.group()))
    for match in CRITICAL.finditer(canonical):
        word = match.group()
        if word == '수있':
            token = 'permission'
        elif word in ('하여야', '해야', '아야', '어야', '여야'):
            token = 'obligation'
        elif word in ('하지않', '아니', '않', '못', '없'):
            token = 'negative'
        else:
            token = word
        items.append((match.start(), token))
    return [value for _, value in sorted(items)]


def assess(target, transcript):
    normalized_target, normalized_transcript = comparable(target), comparable(transcript)
    critical_target, critical_transcript = critical_tokens(normalized_target), critical_tokens(normalized_transcript)
    canonical_cer = JUDGE.cer(normalized_target, normalized_transcript)
    critical_match = critical_target == critical_transcript
    exact = canonical_cer == 0 and critical_match
    return {'status': 'normalized_match' if exact else 'review_required',
            'canonicalCER': round(canonical_cer, 6), 'literalCER': round(JUDGE.cer(target, transcript), 6),
            'criticalMatch': critical_match, 'criticalTarget': critical_target, 'criticalTranscript': critical_transcript,
            'normalizedTarget': normalized_target, 'normalizedTranscript': normalized_transcript,
            'target': target, 'transcript': transcript}


def clip_bytes(clip, pack):
    chunks = []
    for relative in range(0, clip['length'], R.MAX_INDEX_BYTES):
        length = min(R.MAX_INDEX_BYTES, clip['length'] - relative)
        chunks.append(R.public_range(pack['url'], clip['offset'] + relative, length, pack['size']))
    data = b''.join(chunks)
    R.require(len(data) == clip['length'] and R.sha256(data) == clip['sha256'], 'Published clip byte checksum mismatch before ASR')
    return data


def publish_report(client, report):
    """Evidence-only data branch; never writes the production audio manifest or main."""
    R.require(report.get('schema') == 'reader-assistance-asr-report.v1' and report.get('pilot') is True,
              'Only the evidence-only pilot report is published by this command')
    data = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
    prefix = '/repos/' + R.REPOSITORY
    ref_path = prefix + '/git/ref/heads/' + quote(REPORT_BRANCH, safe='/')
    ref = client.request('GET', ref_path, allow404=True)
    parent_sha = ref['object']['sha'] if ref else os.environ.get('GITHUB_SHA', '')
    R.require(re.fullmatch('[a-f0-9]{40}', parent_sha) is not None, 'Pinned report parent required')
    parent = client.request('GET', prefix + '/git/commits/' + parent_sha)
    blob = client.request('POST', prefix + '/git/blobs', {'content': base64.b64encode(data).decode(), 'encoding': 'base64'})
    R.require(blob['sha'] == R.git_blob(data), 'ASR report blob checksum mismatch')
    tree = client.request('POST', prefix + '/git/trees', {'base_tree': parent['tree']['sha'], 'tree': [
        {'path': 'reader-assistance/asr-report.json', 'mode': '100644', 'type': 'blob', 'sha': blob['sha']}]})
    if tree['sha'] == parent['tree']['sha']:
        return {'branch': REPORT_BRANCH, 'commit': parent_sha, 'changed': False}
    commit = client.request('POST', prefix + '/git/commits', {'message': 'Record independent CPU ASR pilot evidence ' + report['sourceDataSha256'][:16],
                                                           'tree': tree['sha'], 'parents': [parent_sha]})
    if ref:
        current = client.request('GET', ref_path)
        R.require(current['object']['sha'] == parent_sha, 'ASR report branch changed; refusing overwrite')
        client.request('PATCH', prefix + '/git/refs/heads/' + quote(REPORT_BRANCH, safe='/'), {'sha': commit['sha'], 'force': False})
    else:
        client.request('POST', prefix + '/git/refs', {'ref': 'refs/heads/' + REPORT_BRANCH, 'sha': commit['sha']})
    return {'branch': REPORT_BRANCH, 'commit': commit['sha'], 'changed': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', default='reader-assistance/pilot.json')
    parser.add_argument('--recipe', default='reader-assistance/recipe.json')
    parser.add_argument('--model-cache', default='reader-assistance-asr-model')
    parser.add_argument('--out', default='reader-assistance-asr-report.json')
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    recipe = json.loads(Path(args.recipe).read_text())
    R.check_recipe(recipe, verify_sources=True)
    data_bytes = Path(args.data).read_bytes()
    plan = R.build_plan(data_bytes, recipe, allow_partial=True)
    R.require(plan['sourceDataSha256'] == PILOT_SOURCE_SHA and plan['expectedEntries'] == 7 and plan['completeEligible'] is False,
              'This first ASR run is limited to the exact seven previously reviewed pilot paragraphs')
    client = R.GitHub()
    state = R.inventory(client, plan)
    R.require(set(plan['tasks']) <= set(state['clips']), 'Pilot synthesis must finish before ASR')

    from huggingface_hub import snapshot_download
    from faster_whisper import WhisperModel
    download_started = time.perf_counter()
    model_path = Path(snapshot_download(repo_id=ASR_MODEL, cache_dir=args.model_cache,
                                       allow_patterns=['config.json', 'model.bin', 'tokenizer.json', 'vocabulary.json', 'preprocessor_config.json']))
    revision = model_path.name
    R.require(re.fullmatch('[a-f0-9]{40}', revision) is not None, 'ASR model snapshot revision missing')
    model = WhisperModel(str(model_path), device='cpu', compute_type='int8', cpu_threads=4, num_workers=1, local_files_only=True)
    loaded_seconds = time.perf_counter() - download_started
    profile = {'modelRepository': ASR_MODEL, 'modelRevision': revision, 'device': 'cpu', 'computeType': 'int8',
               'cpuThreads': 4, 'beamSize': 5, 'language': 'ko', 'vadFilter': False,
               'conditionOnPreviousText': False, 'temperature': 0, 'targetPromptUsed': False,
               'judgeGitBlob': R.git_blob((ROOT / 'research/judge.py').read_bytes()),
               'koVoiceGitBlob': R.git_blob((ROOT / 'public/ko-voice.js').read_bytes()),
               'assessmentGitBlob': R.git_blob(Path(__file__).read_bytes()),
               'fasterWhisperVersion': importlib.metadata.version('faster-whisper'),
               'ctranslate2Version': importlib.metadata.version('ctranslate2')}
    results = {}
    for key, task in plan['tasks'].items():
        clip = state['clips'][key]
        audio = clip_bytes(clip, state['packs'][clip['pack']])
        started = time.perf_counter()
        segments, info = model.transcribe(io.BytesIO(audio), language='ko', task='transcribe', beam_size=5,
                                         condition_on_previous_text=False, vad_filter=False,
                                         temperature=0, initial_prompt=None, word_timestamps=False)
        parts = list(segments)
        transcript = ' '.join(part.text.strip() for part in parts).strip()
        elapsed = time.perf_counter() - started
        result = assess(task['text'], transcript)
        result.update({'audioKey': key, 'mp3Sha256': clip['sha256'], 'textSha256': task['textSha256'],
                       'durationMs': clip['durationMs'], 'asrSeconds': round(elapsed, 3),
                       'segments': [{'start': round(part.start, 3), 'end': round(part.end, 3), 'text': part.text,
                                     'avgLogProbability': round(part.avg_logprob, 6)} for part in parts]})
        results[key] = result
        print('ASR ' + json.dumps(result, ensure_ascii=False), flush=True)
    counts = Counter(item['status'] for item in results.values())
    total_audio = sum(item['durationMs'] for item in results.values()) / 1000
    total_asr = sum(item['asrSeconds'] for item in results.values())
    report = {'schema': 'reader-assistance-asr-report.v1', 'pilot': True, 'sourceDataSha256': plan['sourceDataSha256'],
              'recipeSha256': plan['recipeSha256'], 'asrProfile': profile, 'asrProfileSha256': R.sha256(R.canonical_json(profile)),
              'coverage': {'expectedEntries': 7, 'assessedUniqueClips': len(results), 'allPilotClipsAssessed': len(results) == len(plan['tasks']),
                           'allCorpusAssessed': False},
              'timing': {'modelDownloadAndLoadSeconds': round(loaded_seconds, 3), 'audioSeconds': round(total_audio, 3),
                         'asrSeconds': round(total_asr, 3), 'realtimeFactor': round(total_asr / total_audio, 4),
                         'medianClipAsrSeconds': round(statistics.median(item['asrSeconds'] for item in results.values()), 3)},
              'counts': dict(counts), 'results': results,
              'limitation': 'ASR is independent automated evidence, not direct human listening. Any non-equivalent full-text transcription or critical-token mismatch requires review; no automatic exception or false complete status.'}
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    if args.publish:
        print(json.dumps(publish_report(client, report)), flush=True)
    print('SUMMARY ' + json.dumps({'counts': report['counts'], 'timing': report['timing']}, ensure_ascii=False), flush=True)
    if counts.get('review_required', 0):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
