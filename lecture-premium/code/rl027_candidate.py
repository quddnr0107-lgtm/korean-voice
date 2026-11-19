"""Measure one fully matched donor or one alternative Kim Bora synthesis for RL-027.

Only an authenticated, encrypted candidate leaves the offline production job.
The source release, public transcript, timings and production pins are read-only.
"""
import argparse
import base64
import gc
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import zipfile

import numpy as np

import delivery
import lecture_crypto as crypto
import lecture_pack as pack
import rl027_audio_check as check
from namespace_guard import validate_snapshot
from rl027_candidate_text import SOURCE_DIGEST, score_spoken_words, validate_donor

ROOT = Path(__file__).resolve().parents[2]
LESSON = check.LESSON
SPEAKER = 'Sohee'
RATE = check.RATE
TARGET, DONOR = 44, 97
REFERENCE_SOURCE_SHA = '2cc1a75aed34651c62ad523ac0c55fdcdc5fbea44d974984f40c917279748b5f'
SPOKEN_SHA = '33af2bb8bab15639b0f4542b29ae0be07c8b4e952cf27f8f6074dfcc4972e943'
SCHEMA = 'rl027-unit-44-voice-candidate/v1'


def require(condition, code):
    if not condition:
        raise ValueError(code)


def check_request(path):
    request = json.loads(path.read_text())
    require(request == {
        'request': 'rl027-unit44-audible-correction-20261119',
        'mode': 'encrypted-candidate-only', 'source_descriptor_sha256': check.DESCRIPTOR_DIGEST,
        'render_revision': 'clause-bounded-reading-v2', 'repair_units': [44, 97],
        'lesson_id': LESSON, 'unit': TARGET, 'identical_reading_donor': DONOR, 'alternate_seed': 7319,
    }, 'request_scope')


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()


def guarded():
    validate_snapshot(os.getuid(), os.getuid(), socket.if_nameindex(),
                      Path('/proc/net/route').read_text(), os.readlink('/proc/self/ns/net'),
                      os.environ['BORA_HOST_NETNS'], Path('/proc/net/ipv6_route').read_text())


def decoded(mp3):
    # Input must be seekable so the pinned decoder honors LAME end padding.
    with os.fdopen(os.memfd_create('rl027-part'), 'w+b') as stream:
        stream.write(mp3)
        stream.seek(0)
        result = subprocess.run(
            ['ffmpeg', '-nostdin', '-v', 'error', '-i', f'/proc/self/fd/{stream.fileno()}',
             '-ar', str(RATE), '-ac', '1', '-f', 's16le', 'pipe:1'],
            pass_fds=(stream.fileno(),), capture_output=True, timeout=90, check=True,
            env={'PATH': os.defpath, 'LANG': 'C'})
    return result.stdout


class Original:
    def __init__(self, directory, key):
        raw = (directory / f'{LESSON}.descriptor.json').read_bytes()
        require(sha256(raw).hexdigest() == check.DESCRIPTOR_DIGEST, 'descriptor_pin')
        descriptor = delivery.check_descriptor(json.loads(raw), LESSON)
        self.audio = descriptor['audio']
        self.key = key
        encrypted = directory / delivery.media_name(LESSON, 'audio', self.audio)
        self.cipher = encrypted.read_bytes()
        pack.validate_pack(self.audio, self.cipher)
        self.cache = {}
        meta = descriptor['content']
        sealed = (directory / delivery.media_name(LESSON, 'content', meta)).read_bytes()
        plain, _ = crypto.open_range(key, meta, sealed, expected_id=LESSON,
                                     expected_kind='private-metadata', expected_key_id=delivery.KEY_ID)
        self.record = json.loads(plain)
        source = self.record['narration_text']
        original_lesson = self.record['lesson_source']
        actual_sha = sha256(source.encode()).hexdigest()
        require(self.record['id'] == LESSON and original_lesson['id'] == LESSON
                and self.record['audio_pack_sha256'] == self.audio['pack_sha256']
                and self.audio['sample_rate'] == RATE
                and actual_sha == self.record['source_sha256'] == original_lesson['source_sha256']
                and ''.join(chunk['text'] for chunk in original_lesson['chunks']) == source,
                'record_pin')
        print(json.dumps({'producer_original_source_verified': True,
                          'reference_source_hash_matches': actual_sha == REFERENCE_SOURCE_SHA}), flush=True)


    def unit(self, index):
        segment = self.record['segments'][index]
        begin, end = segment['start_sample'], segment['end_sample']
        count = end - begin
        require(0 < count < 60 * RATE, 'unit_length')
        for part in self.audio['parts']:
            if part['start_sample'] <= begin and end <= part['end_sample']:
                i = part['part_index']
                if i not in self.cache:
                    start = part['pack_offset']
                    data = pack.open_part(self.key, part, self.cipher[start:start + part['cipher_bytes']],
                                          expected_id=LESSON, expected_part_index=i,
                                          expected_pack_id=self.audio['object_id'])
                    pcm = decoded(data)
                    require(abs(len(pcm) // 2 - (part['end_sample'] - part['start_sample'])) <= 1,
                            'mp3_decode_length')
                    self.cache[i] = pcm
                offset = 2 * (begin - part['start_sample'])
                pcm = self.cache[i][offset:offset + 2 * count]
                require(len(pcm) == 2 * count, 'incomplete_unit')
                return pcm
        raise ValueError('unit_crosses_part_boundary')


def target_plan(record):
    sys.path.insert(0, str(ROOT / 'bora-production'))
    import planning
    import rl_pronunciation
    plan = rl_pronunciation.apply_to_plan(planning.build_plan(LESSON, record['narration_text']))
    require(len(plan['units']) == len(record['segments']) == 191, 'plan_size')
    for i in (TARGET, DONOR):
        unit = plan['units'][i]
        segment = record['segments'][i]
        require(unit['id'] == segment['unit_id'] and unit['mode'] == segment['mode'] == 'reading'
                and unit['source'] == segment['text'] and unit['spoken_sha256'] == SPOKEN_SHA
                and unit['start_utf16'] == segment['start_utf16']
                and unit['end_utf16'] == segment['end_utf16']
                and unit['reading_mode_verified'] and not unit['review'], 'plan_identity')
    return plan['units'][TARGET]


def load_recognizer(model):
    sys.path.insert(0, str(ROOT / 'bora-production'))
    import tb001_asr_crosscheck as asr
    from faster_whisper import WhisperModel
    asr.verify_model(model)
    return WhisperModel(str(model), device='cpu', compute_type='int8', cpu_threads=4,
                        local_files_only=True), asr.TRANSCRIBE


def has_dropout(acoustic):
    quiet = acoustic['quiet_runs']
    tones = acoustic['tone_runs'] + acoustic['stationary_spectrum_runs']
    if any(span['to_s'] - span['from_s'] >= 3.0 for span in quiet):
        return True
    for tone in tones:
        length = tone['to_s'] - tone['from_s']
        if length >= 1.0:
            return True
        # RL-027 had about half a second of low-pitch buzz and a nearby 1.4s
        # dropout. Benign pauses and short voiced notes occur in our controls.
        if length >= .45 and any(gap['to_s'] - gap['from_s'] >= 1.2
                                 and -.1 <= gap['from_s'] - tone['to_s'] <= 1.0 for gap in quiet):
            return True
    return False


def score_candidate(pcm, recognizer):
    model, params = recognizer
    duration = len(pcm) / (2 * RATE)
    require(2 <= duration <= 32, 'candidate_duration')
    samples = np.frombuffer(pcm, dtype='<i2')
    checks = []
    for speed in (1.0, 1.2):
        signal = check.playback_signal(pcm, speed)
        acoustic = check.measurements(signal, 0, speed)
        words, _ = model.transcribe(signal, **params)
        score = score_spoken_words(''.join(piece.text for piece in words))
        approved = (score['accepted'] and not has_dropout(acoustic)
                    and -40 < acoustic['rms_dbfs'] < -3
                    and np.mean(np.abs(samples.astype('int32')) >= 32766) < .0001)
        checks.append(dict(speed=speed, acoustic=acoustic, words=score, accepted=bool(approved)))
    return all(item['accepted'] for item in checks), checks


def fit_candidate(pcm, target_samples):
    """Preserve the old slot only when timing adjustment is within 5% and sample edges are quiet."""
    count = len(pcm) // 2
    ratio = count / target_samples
    if count == target_samples:
        return pcm, 'unchanged'
    if not .95 <= ratio <= 1.05:
        return pcm, 'natural_duration'
    fitted = check.media_bytes(['-f', 's16le', '-ar', str(RATE), '-ac', '1', '-i', 'pipe:0',
                                '-af', f'atempo={ratio:.10f}', '-ar', str(RATE), '-ac', '1',
                                '-f', 's16le', 'pipe:1'], pcm)
    delta = target_samples - len(fitted) // 2
    # Stretch filter granularity may vary. Do not cut a voiced ending.
    quiet_window = max(-delta + RATE // 250, RATE // 100)
    ending = np.frombuffer(fitted[-2 * quiet_window:], dtype='<i2').astype('float32')
    if abs(delta) > RATE // 25 or np.sqrt(np.mean(ending ** 2)) > 220:
        return pcm, 'natural_duration'
    fitted = fitted[:target_samples * 2].ljust(target_samples * 2, b'\0')
    return fitted, 'matched_duration'


def split_reading(unit, source_anchor, spoken_anchor):
    """Partition the pinned reading, retaining exact source/voice text and offsets."""
    source, spoken = unit['source'], unit['spoken']
    require(source.count(source_anchor) == spoken.count(spoken_anchor) == 1
            and not unit['review'] and not unit.get('critical_terms'), 'split_identity')
    source_at, spoken_at = source.index(source_anchor), spoken.index(spoken_anchor)
    require(0 < source_at < len(source) and 0 < spoken_at < len(spoken), 'split_identity')
    parts = []
    for start, end, text in ((0, source_at, spoken[:spoken_at]),
                             (source_at, len(source), spoken[spoken_at:])):
        body = source[start:end]
        start16 = unit['start_utf16'] + len(source[:start].encode('utf-16-le')) // 2
        parts.append(dict(id=unit['id'], paragraph_id=unit['paragraph_id'], mode=unit['mode'],
                          source=body, spoken=text, source_sha256=sha256(body.encode()).hexdigest(),
                          spoken_sha256=sha256(text.encode()).hexdigest(),
                          start_cp=unit['start_cp'] + start, end_cp=unit['start_cp'] + end,
                          start_utf16=start16, end_utf16=start16 + len(body.encode('utf-16-le')) // 2,
                          reading_mode_verified=unit['reading_mode_verified'], review=[], critical_terms=[]))
    require(parts[-1]['end_cp'] == unit['end_cp'] and parts[-1]['end_utf16'] == unit['end_utf16'],
            'split_identity')
    return parts


def render_phrase(renderer, unit, destination, position):
    receipt = renderer.render_unit(unit, destination)
    # Export only fixed stage labels and numeric quality results. The full receipt
    # contains protected speech and must remain inside the offline child.
    attempts = [dict(seed=c['seed'], eos=c.get('natural_eos') is True,
                     signal=c.get('signal', {}).get('blocked') is False,
                     errors=c.get('asr', {}).get('character_errors', -1))
                for c in receipt['candidates']]
    print(json.dumps(dict(phrase=position, passed=receipt['status'] == 'passed', attempts=attempts)), flush=True)
    if receipt['status'] != 'passed':
        return None
    require(receipt['natural_eos'] is True and receipt['production_eligible']
            and receipt['automatic_checks_passed'] and not receipt['blocked']
            and receipt['seed'] in renderer.seeds and receipt['file'] == 'final.wav'
            and not receipt['asr']['blocked'] and receipt['spoken_sha256'] == unit['spoken_sha256'],
            'synth_receipt')
    raw = (destination / 'final.wav').read_bytes()
    require(sha256(raw).hexdigest() == receipt['sha256'], 'generated_waveform_digest')
    return decoded(raw)


def synthesize(unit_path, output, model, renderer_asr):
    guarded()
    require('ADMIN_LAW_PREMIUM_MEDIA_KEY_B64' not in os.environ and
            'ADMIN_LAW_PREMIUM_SOURCE_KEY_B64' not in os.environ, 'keys_in_synth')
    sys.path.insert(0, str(ROOT / 'bora-production'))
    import fp32_renderer as base
    unit = json.loads(unit_path.read_text())
    require(unit['mode'] == 'reading' and unit['spoken_sha256'] == SPOKEN_SHA
            and sha256(unit['spoken'].encode()).hexdigest() == SPOKEN_SHA
            and sha256(unit['source'].strip().encode()).hexdigest() == SOURCE_DIGEST
            and unit['id'] == 'RL-027:p0032:u001' and not unit['review'], 'synth_scope')
    require(base.SEEDS == (5192, 7319), 'producer_seed_pair')
    renderer = base.FP32Renderer(str(model), str(renderer_asr), max_attempts=2)
    require(renderer.settings['speaker'] == SPEAKER and renderer.settings['dtype'] == 'float32'
            and renderer.seeds == base.SEEDS, 'original_voice')
    output.mkdir(mode=0o700)
    introduction, penalty = split_reading(unit, '3년', '삼 년')
    phrases = []
    for label, phrase, source_anchor, spoken_anchor in (
            ('conduct', introduction, '정치운동', '정치운동'),
            ('penalty', penalty, '또는', '또는')):
        pcm = render_phrase(renderer, phrase, output / label, label)
        if pcm is not None:
            phrases.append(pcm)
            continue
        for i, part in enumerate(split_reading(phrase, source_anchor, spoken_anchor)):
            sublabel = f'{label}-{i}'
            pcm = render_phrase(renderer, part, output / sublabel, sublabel)
            require(pcm is not None, 'subphrase_failed')
            phrases.append(pcm)
    # Use the approved renderer’s already faded, quiet boundaries and normal
    # within-paragraph gap. The resulting complete sentence is checked twice.
    gap = bytes(2 * round(RATE * renderer.settings['unit_gap_s']))
    (output / 'joined.s16le').write_bytes(gap.join(phrases))


SAFE_RENDER_FAILURES = frozenset({
    'subphrase_failed', 'split_identity', 'original_voice', 'producer_seed_pair',
    'synth_receipt', 'synth_scope', 'generated_waveform_digest', 'keys_in_synth',
    'renderer_must_be_unprivileged', 'offline_namespace_guard_failed', 'runtime_model_load_failed',
    'runtime_package_missing', 'runtime_package_version_mismatch', 'model_directory_invalid',
    'runtime_dtype_or_threads_mismatch', 'unit_critical_terms_invalid', 'MemoryError', 'OSError',
    'TimeoutExpired', 'RenderFailure', 'ValueError', 'RuntimeError', 'KeyError',
})


def is_public_render_report(row):
    if not isinstance(row, dict) or set(row) != {'phrase', 'passed', 'attempts'}:
        return False
    phrases = {'conduct', 'penalty', 'conduct-0', 'conduct-1', 'penalty-0', 'penalty-1'}
    return (isinstance(row['phrase'], str) and row['phrase'] in phrases
            and type(row['passed']) is bool and isinstance(row['attempts'], list)
            and len(row['attempts']) <= 2 and all(
                isinstance(c, dict) and set(c) == {'seed', 'eos', 'signal', 'errors'}
                and c['seed'] in (5192, 7319) and type(c['eos']) is bool and type(c['signal']) is bool
                and type(c['errors']) is int and -1 <= c['errors'] <= 500 for c in row['attempts']))


def seal_candidate(outdir, key, pcm, manifest):
    require(not outdir.exists(), 'output_exists')
    outdir.mkdir(mode=0o700)
    envelope = io.BytesIO()
    with zipfile.ZipFile(envelope, 'w', compression=zipfile.ZIP_STORED) as data:
        data.writestr('candidate.json', canonical(manifest))
        data.writestr('unit.s16le', pcm)
    raw = envelope.getvalue()
    header, cipher = crypto.seal(key, raw, LESSON, 'checkpoint', delivery.KEY_ID)
    recovered, _ = crypto.open_range(key, header, cipher, expected_id=LESSON,
                                     expected_kind='checkpoint', expected_key_id=delivery.KEY_ID)
    require(recovered == raw, 'roundtrip')
    (outdir / 'RL-027.unit44.candidate.sealed.json').write_bytes(canonical(header))
    (outdir / 'RL-027.unit44.candidate.sealed.bin').write_bytes(cipher)


def build(directory, output, model, voice, renderer_asr):
    check_request(ROOT / 'lecture-premium' / 'rl027-unit44-request.json')
    guarded()
    key = base64.b64decode(os.environ.pop('ADMIN_LAW_PREMIUM_MEDIA_KEY_B64'), validate=True)
    original = Original(directory, key)
    source = original.record
    identical_wav = validate_donor(source)
    unit = target_plan(source)
    original_pcm = original.unit(TARGET)
    target = source['segments'][TARGET]
    target_n = target['end_sample'] - target['start_sample']
    require(round(target['start_s'], 2) == 702.56 and round(target['end_s'], 2) == 718.23,
            'target_timeline')
    recognizer = load_recognizer(model)
    for kind in ('donor97', 'clause_renderer'):
        if kind == 'donor97':
            # Equal synthesis checkpoints do not prove independently encoded MP3
            # excerpts have equal playback. Measure the released donor itself.
            pcm = original.unit(DONOR)
            print(json.dumps({'candidate': kind, 'donor_start_s': round(source['segments'][DONOR]['start_s'], 3),
                              'donor_end_s': round(source['segments'][DONOR]['end_s'], 3),
                              'identical_source_waveform': identical_wav,
                              'identical_released_pcm': pcm == original_pcm}), flush=True)
        else:
            # Keep the large recognizer and TTS models out of memory together.
            del recognizer
            gc.collect()
            with tempfile.TemporaryDirectory(prefix='rl027-') as tmp:
                work = Path(tmp)
                unit_path = work / 'unit.json'
                unit_path.write_bytes(canonical(unit))
                wav = work / 'generated'
                # Third-party stdout can contain protected speech. Accept only
                # structured aggregate metrics, even on a signal or OOM.
                proc = subprocess.run([sys.executable, __file__, 'synthesize', str(unit_path), str(wav),
                                       str(voice), str(renderer_asr)], capture_output=True, timeout=3600)
                for line in proc.stdout.splitlines():
                    prefix = b'RL-027 audio candidate stage failed: '
                    if line.startswith(prefix):
                        reason = line[len(prefix):].decode('ascii', errors='replace')
                        if reason in SAFE_RENDER_FAILURES:
                            print(json.dumps(dict(render_failure=reason)), flush=True)
                        continue
                    try:
                        row = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    if is_public_render_report(row):
                        print(json.dumps(row), flush=True)
                print(json.dumps(dict(renderer_exit=proc.returncode)), flush=True)
                require(proc.returncode == 0, 'clause_renderer_failed')
                pcm = (wav / 'joined.s16le').read_bytes()
            recognizer = load_recognizer(model)
        pcm, fit = fit_candidate(pcm, target_n)
        if pcm == original_pcm:
            continue
        accepted, results = score_candidate(pcm, recognizer)
        summary = dict(candidate=kind, timing=fit, original_samples=target_n,
                       candidate_samples=len(pcm)//2, accepted=accepted, checks=results)
        print(json.dumps(summary, ensure_ascii=True), flush=True)
        if accepted:
            evidence = dict(schema=SCHEMA, lesson_id=LESSON, unit_index=TARGET, donor_index=DONOR if kind=='donor97' else None,
                            source_descriptor_sha256=check.DESCRIPTOR_DIGEST, speaker=SPEAKER,
                            spoken_sha256=SPOKEN_SHA, original_start_sample=target['start_sample'],
                            original_end_sample=target['end_sample'], pcm_sha256=sha256(pcm).hexdigest(),
                            pcm_format='s16le', channels=1, rate=RATE, candidate=summary)
            seal_candidate(output, key, pcm, evidence)
            return
    raise ValueError('no_candidate_passed')


if __name__ == '__main__':
    try:
        os.umask(0o077)
        parser = argparse.ArgumentParser()
        commands = parser.add_subparsers(dest='command', required=True)
        for name, fields in [('build', ['directory','output','model','voice','renderer_asr']),
                             ('synthesize', ['unit_path','output','model','renderer_asr'])]:
            command = commands.add_parser(name)
            for field in fields:
                command.add_argument(field, type=Path)
        values = vars(parser.parse_args())
        action = values.pop('command')
        (build if action == 'build' else synthesize)(**values)
    except Exception as error:
        # Only closed failure categories may leave the runner, never private text.
        known = {'descriptor_pin', 'record_pin', 'request_scope', 'plan_size', 'plan_identity',
                 'target_timeline', 'no_candidate_passed', 'candidate_duration', 'original_voice',
                 'synth_receipt', 'clause_renderer_failed', 'split_identity', 'subphrase_failed', 'generated_waveform_digest', 'mp3_decode_length', 'output_exists',
                 'incomplete_unit', 'unit_crosses_part_boundary', 'unit_length', 'roundtrip'}
        category = str(error) if str(error) in known | SAFE_RENDER_FAILURES else type(error).__name__
        print('RL-027 audio candidate stage failed:', category, flush=True)
        raise SystemExit(1) from None
