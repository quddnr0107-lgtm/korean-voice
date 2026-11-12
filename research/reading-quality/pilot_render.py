"""Private, resumable Kim Bora pilot renderer; never uploads or deploys.

Run ONLY inside a new Linux network namespace with BORA_HOST_NETNS set to the
parent namespace. Models must already be downloaded. Input is paragraph_plan.py
JSON. Optional unit.critical_terms is a list reviewed against that unit's spoken
text. ASR disagreement is a review signal, not proof of a speech defect.
"""
import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unicodedata

from paragraph_plan import measured_timeline
from quality import number

MODEL = 'Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice'
MODEL_REVISION = '0c0e3051f131929182e2c023b9537f8b1c68adfe'
ASR_REVISION = '536b0662742c02347bc0e980a01041f333bce120'
MAX_REVIEW_AUDIO_BYTES = 16 * 1024 * 1024
STYLES = {
    'explain': 'Speak Korean to one learner. Ask the questions with genuine curiosity, then explain with clear, conversational phrasing. Keep a consistent speaking voice throughout the paragraph. Allow occasional quiet, unforced inhalations and small thoughtful pauses at natural phrase boundaries when the meaning calls for them. Keep the vocal affect appropriate to the subject, and keep the spoken words clear and complete.',
    'reading': 'Formal recitation of a Korean legal provision. Crisp, distinct consonants and a confident, clearly projected voice. Maintain steady supported vocal energy through every final syllable. Measured phrasing with clear pauses at clause boundaries.',
}
FILTERS = {
    'explain': 'highpass=f=65,loudnorm=I=-18:TP=-2:LRA=11,alimiter=limit=0.84:level=false',
    'reading': 'highpass=f=65,equalizer=f=2600:t=q:w=0.85:g=1.2,acompressor=threshold=0.08:ratio=2:attack=15:release=120:makeup=1,loudnorm=I=-16:TP=-2:LRA=7,alimiter=limit=0.89:level=false',
}


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def identity(value):
    # Shared with the protected wrapper/config: sorted compact ASCII JSON.
    return digest_bytes(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                   separators=(',', ':')).encode('ascii'))


def file_hash(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def canonical(text):
    text = unicodedata.normalize('NFKC', text)
    # ASR often emits Arabic digits for the exact Sino-Korean reading.
    text = re.sub(r'(?<!\d)\d{1,4}(?!\d)', lambda m: number(m[0]), text)
    return ''.join(c for c in text if c.isalnum())


def edit_distance(a, b):
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        row = [i]
        for j, y in enumerate(b, 1):
            row.append(min(row[-1] + 1, prev[j] + 1, prev[j - 1] + (x != y)))
        prev = row
    return prev[-1]


def transcript_score(expected, recognized, critical_terms=(), max_cer=.08):
    a, b = canonical(expected), canonical(recognized)
    if not a:
        raise ValueError('empty spoken text')
    errors = edit_distance(a, b)
    missing = [x for x in critical_terms if canonical(x) not in b]
    cer = errors / len(a)
    return dict(character_errors=errors, expected_characters=len(a), cer=cer,
                missing_critical=missing, blocked=bool(missing or cer > max_cer),
                review_required=bool(errors or missing), perceptual_review_required=True)


def candidate_rank(candidate):
    return (bool(candidate['signal']['blocked']), bool(candidate['asr']['blocked']),
            len(candidate['asr']['missing_critical']), candidate['asr']['character_errors'],
            candidate['seed'])


def choose_candidate(candidates):
    if not candidates:
        raise ValueError('no candidates')
    return min(candidates, key=candidate_rank)


def candidate_cannot_be_beaten(candidate, remaining_seeds):
    """Prove selection is final using the selector's exact lower bound.

    A future candidate can at best rank (False, False, 0, 0, its_seed).
    Equal ranks keep the already-generated candidate because min() is stable.
    This skips redundant synthesis, never relaxes transcript/signal checks.
    """
    asr=candidate['asr'];seed=candidate['seed']
    if (candidate['signal'].get('blocked') is not False or asr.get('blocked') is not False
            or asr.get('missing_critical') != [] or type(asr.get('character_errors')) is not int
            or asr['character_errors'] != 0 or type(seed) is not int):
        return False
    return all(type(other) is int and seed <= other for other in remaining_seeds)


def save_json(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + '.partial')
    with tmp.open('w', encoding='utf-8') as f:
        os.chmod(tmp, 0o600)
        json.dump(value, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def assert_offline():
    original = os.environ.get('BORA_HOST_NETNS')
    if not original or os.readlink('/proc/self/ns/net') == original:
        raise RuntimeError('rendering requires an isolated network namespace')
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                      HF_HUB_DISABLE_TELEMETRY='1', DO_NOT_TRACK='1',
                      ORT_DISABLE_TELEMETRY='1')


def signal_stats(wave, sr, max_seconds=None):
    import numpy as np
    a = np.asarray(wave)
    finite = bool(np.isfinite(a).all())
    mono = a.ndim == 1
    duration = len(a) / sr
    peak = float(np.max(np.abs(a))) if a.size and finite else None
    rms = float(np.sqrt(np.mean(a.astype('float64') ** 2))) if a.size and finite else None
    clipped = int(np.count_nonzero(np.abs(a) >= 1)) if finite else 0
    reasons = []
    if not finite or not mono or not a.size:
        reasons.append('invalid_waveform')
    if rms is None or rms < 1e-5:
        reasons.append('silent_waveform')
    if clipped:
        reasons.append('clipped_waveform')
    if max_seconds is not None and duration >= max_seconds:
        reasons.append('possible_token_cap_truncation')
    return dict(sample_rate=sr, samples=len(a), seconds=duration, peak=peak,
                rms=rms, clipped_samples=clipped, reasons=reasons, blocked=bool(reasons))


def trimmed_faded(wave, sr):
    import numpy as np
    a = np.asarray(wave, dtype='float32').copy()
    frame = max(1, round(sr * .02))
    padded = np.pad(a, (0, (-len(a)) % frame))
    energy = np.sqrt(np.mean(padded.reshape(-1, frame) ** 2, axis=1))
    active = np.flatnonzero(energy > max(float(energy.max()) * .01, 1e-6))
    if not len(active):
        raise ValueError('no active speech')
    keep = round(sr * .12)
    lo = max(0, int(active[0] * frame) - keep)
    hi = min(len(a), int((active[-1] + 1) * frame) + keep)
    a = a[lo:hi]
    ramp = min(round(sr * .005), len(a) // 2)
    if ramp:
        a[:ramp] *= np.linspace(0, 1, ramp)
        a[-ramp:] *= np.linspace(1, 0, ramp)
    return a, dict(removed_leading_samples=lo, removed_trailing_samples=len(wave)-hi,
                   retained_edge_ms=120, fade_ms=5)


def unit_key(plan, unit, settings):
    return identity(dict(source_sha256=plan['source_sha256'], unit=unit, settings=settings))


def reusable_record(record, expected_key, directory, kind):
    if record.get('key') != expected_key or record.get('kind') != kind:
        return False
    name = record.get('file')
    if not isinstance(name, str) or Path(name).name != name:
        return False
    path = directory / name
    return path.is_file() and file_hash(path) == record.get('sha256')


def quarantine_review_candidate(candidates, directory, index, expected_key, sf, max_seconds):
    """Keep one signal-valid raw candidate for listening; never an accepted unit."""
    usable = [c for c in candidates if c['signal'].get('blocked') is False]
    if not usable:
        return {}
    candidate = choose_candidate(usable)
    name = candidate.get('file')
    if (type(index) is not int or index < 0 or candidate.get('key') != expected_key
            or type(candidate.get('seed')) is not int
            or name != f'candidate-{candidate["seed"]}.wav'
            or not re.fullmatch('[a-f0-9]{64}', candidate.get('sha256',''))):
        raise ValueError('review candidate identity')
    directory = Path(directory)
    folder = directory/'units'/f'{index:04d}'
    source = folder/name
    if any(p.is_symlink() for p in (directory, directory/'units', folder, source)):
        raise ValueError('review candidate symlink')
    if not source.is_file() or not 0 < source.stat().st_size <= MAX_REVIEW_AUDIO_BYTES:
        raise ValueError('review candidate size')
    raw = source.read_bytes()
    if not 0 < len(raw) <= MAX_REVIEW_AUDIO_BYTES or digest_bytes(raw) != candidate['sha256']:
        raise ValueError('review candidate integrity')
    # Recheck the bytes actually delivered, not only the cached signal claim.
    wave, sr = sf.read(io.BytesIO(raw), dtype='float32')
    signal = signal_stats(wave, sr, max_seconds)
    if (signal['blocked'] or signal['samples'] != candidate['signal']['samples']
            or sr != candidate['signal']['sample_rate']):
        raise ValueError('review candidate signal')
    target = folder/'review.wav'
    if target.is_symlink():
        raise ValueError('review output symlink')
    with tempfile.NamedTemporaryFile(dir=folder, prefix='.review-', suffix='.wav', delete=False) as file:
        temp = Path(file.name)
        file.write(raw)
    try:
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    return dict(review_file='review.wav', review_sha256=digest_bytes(raw), review_size_bytes=len(raw),
                review_seed=candidate['seed'], review_signal=signal, review_only=True,
                production_eligible=False, review_kind='raw_candidate_for_listening_only')


def export_subset(plan, records, start, stop, directory, indices=None, settings=None):
    """Private checkpoint manifest; caller may encrypt it for remote delivery."""
    rows = []
    indices = list(range(start, stop)) if indices is None else list(indices)
    if len(indices) != len(records):
        raise ValueError('subset record count mismatch')
    for index, record in zip(indices, records):
        unit = plan['units'][index]
        row = dict(record, unit_index=index, unit_id=unit['id'], paragraph_id=unit['paragraph_id'],
                   start_utf16=unit['start_utf16'], end_utf16=unit['end_utf16'],
                   spoken_sha256=digest_bytes(unit['spoken'].encode()))
        if not row.get('blocked'):
            if row.get('review_file') is not None:
                raise ValueError('review audio cannot be an accepted unit')
            row['relative_file'] = f'units/{index:04d}/{record["file"]}'
        elif row.get('review_file') is not None:
            if (row['review_file'] != 'review.wav' or row.get('review_only') is not True
                    or row.get('production_eligible') is not False or 'file' in row or 'relative_file' in row):
                raise ValueError('invalid quarantined review audio')
            row['review_relative_file'] = f'units/{index:04d}/review.wav'
        rows.append(row)
    manifest = dict(schema='kim-bora-private-subset/v1', lesson_id=plan['lesson_id'],
        source_sha256=plan['source_sha256'], plan_sha256=identity(plan),
        unit_start=start, unit_end_exclusive=stop, total_plan_units=len(plan['units']),
        unit_indices=indices, units=rows, perceptual_review_required=True, sentence_alignment_verified=False,
        status='blocked_requires_review' if any(r.get('blocked') for r in rows) else 'private_pcm_subset_complete')
    if settings is not None:
        manifest['render_settings'] = settings
        manifest['render_settings_sha256'] = identity(settings)
    save_json(directory/'subset.json', manifest)
    return manifest


def check_plan(plan):
    # measured_timeline verifies raw source reconstruction, hashes, identity,
    # UTF-16 boundaries and each unit's containment in its declared paragraph.
    rows = [dict(id=u['id'], source_sha256=u['source_sha256'],
                 measurement_kind='final_decoded_pcm', sample_rate=24000,
                 sample_count=1) for u in plan['units']]
    measured_timeline(plan, dict(source_sha256=plan['source_sha256'], units=rows))
    for u in plan['units']:
        if u['mode'] not in STYLES or not u['spoken'].strip():
            raise ValueError('invalid mode/spoken text')
        for term in u.get('critical_terms', []):
            if not canonical(term) or canonical(term) not in canonical(u['spoken']):
                raise ValueError('critical term not present in spoken text')


class Renderer:
    def __init__(self, args, plan):
        assert_offline()
        import onnxruntime as ort
        ort.disable_telemetry_events()
        import torch
        import soundfile as sf
        from qwen_tts import Qwen3TTSModel
        from faster_whisper import WhisperModel
        self.torch, self.sf = torch, sf
        self.args, self.plan = args, plan
        torch.set_num_threads(args.threads)
        self.model = Qwen3TTSModel.from_pretrained(str(args.model), device_map='cpu',
            dtype=torch.bfloat16, attn_implementation='sdpa',
            low_cpu_mem_usage=True, local_files_only=True)
        self.asr = WhisperModel(str(args.asr), device='cpu', compute_type='int8',
            cpu_threads=1, num_workers=1, local_files_only=True)
        self.settings = dict(schema='kim-bora-pilot-render/v1', model=MODEL,
            expected_model_revision=MODEL_REVISION, expected_asr_revision=ASR_REVISION,
            local_model_sha256=file_hash(args.model/'model.safetensors'),
            local_tokenizer_sha256=file_hash(args.model/'speech_tokenizer/model.safetensors'),
            local_asr_sha256=file_hash(args.asr/'model.bin'), speaker='Sohee',
            styles=STYLES, filters=FILTERS, seeds=[5192,7319], temperature=.8, top_p=.9,
            max_new_tokens=args.max_new_tokens, sample_rate=args.sample_rate,
            max_cer=args.max_cer, paragraph_gap_s=args.paragraph_gap,
            unit_gap_s=args.unit_gap, pitch_shift=0, tempo_factor=1,
            torch_version=torch.__version__, perceptual_review_required=True)
        self.directory = args.output
        save_json(self.directory/'settings.json', self.settings)

    def recognize(self, wave, sr):
        import numpy as np
        from scipy.signal import resample_poly
        a = resample_poly(wave, 16000//math.gcd(16000,sr), sr//math.gcd(16000,sr)).astype('float32')
        segments, _ = self.asr.transcribe(a, language='ko', beam_size=5,
            temperature=0, condition_on_previous_text=False, vad_filter=False,
            word_timestamps=True)
        segments = list(segments)
        return dict(recognized=' '.join(s.text.strip() for s in segments),
            words=[dict(text=w.word, start_s=w.start, end_s=w.end, probability=w.probability)
                   for s in segments for w in (s.words or [])],
            timing_basis='raw_candidate_asr_not_forced_source_alignment')

    def candidate(self, unit, index, seed, key):
        folder = self.directory/'units'/f'{index:04d}'
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        record_path = folder/f'candidate-{seed}.json'
        if record_path.is_file():
            record = json.loads(record_path.read_text())
            if reusable_record(record, key, folder, 'candidate'):
                return record
        self.torch.manual_seed(seed)
        waves, sr = self.model.generate_custom_voice(text=unit['spoken'], language='Korean',
            speaker='Sohee', instruct=STYLES[unit['mode']], non_streaming_mode=True,
            max_new_tokens=self.args.max_new_tokens, do_sample=True, temperature=.8, top_p=.9)
        wave = waves[0]
        signal = signal_stats(wave, sr, self.args.max_new_tokens/12.5*.97)
        name = f'candidate-{seed}.wav'
        self.sf.write(folder/name, wave, sr, subtype='FLOAT')
        asr = self.recognize(wave, sr) if not signal['blocked'] else dict(recognized='', words=[])
        asr.update(transcript_score(unit['spoken'], asr['recognized'], unit.get('critical_terms', []), self.args.max_cer))
        record = dict(kind='candidate', key=key, seed=seed, file=name,
            sha256=file_hash(folder/name), signal=signal, asr=asr)
        save_json(record_path, record)
        print(json.dumps(dict(event='candidate', unit=index, seed=seed,
            seconds=signal['seconds'], errors=asr['character_errors'], blocked=signal['blocked'] or asr['blocked'])), flush=True)
        return record

    def candidate_pool(self, unit, index, key):
        candidates=[]
        seeds=self.settings['seeds']
        for position,seed in enumerate(seeds):
            candidates.append(self.candidate(unit,index,seed,key))
            best=choose_candidate(candidates)
            if candidate_cannot_be_beaten(best,seeds[position+1:]):
                break
        return candidates

    def render_unit(self, unit, index):
        key = unit_key(self.plan, unit, self.settings)
        folder = self.directory/'units'/f'{index:04d}'
        done = folder/'selected.json'
        if done.is_file():
            record = json.loads(done.read_text())
            if reusable_record(record, key, folder, 'selected'):
                print(json.dumps(dict(event='reused', unit=index)), flush=True)
                return record
        candidates = self.candidate_pool(unit,index,key)
        selected = choose_candidate(candidates)
        blocked = selected['signal']['blocked'] or selected['asr']['blocked']
        if blocked:
            save_json(folder/'review.json', dict(unit_id=unit['id'], key=key,
                status='blocked_requires_review', candidates=candidates))
            # One signal-valid raw candidate remains available ONLY for private
            # listening review; ASR blockage and production assembly stay blocked.
            diagnostics = [dict(seed=c['seed'], signal=c['signal'], asr=c['asr']) for c in candidates]
            review = quarantine_review_candidate(candidates, self.directory, index, key,
                self.sf, self.args.max_new_tokens/12.5*.97)
            return dict(blocked=True, unit_id=unit['id'], key=key,
                        candidate_diagnostics=diagnostics, **review)
        processed = folder/'processed.wav'
        subprocess.run(['ffmpeg','-v','error','-y','-i',str(folder/selected['file']),
            '-af',FILTERS[unit['mode']],'-ar',str(self.args.sample_rate),'-ac','1',
            '-c:a','pcm_f32le',str(processed)], check=True)
        wave, sr = self.sf.read(processed, dtype='float32')
        wave, trim = trimmed_faded(wave, sr)
        final = folder/'final.wav'
        self.sf.write(final, wave, sr, subtype='FLOAT')
        decoded, final_sr = self.sf.read(final, dtype='float32')
        signal = signal_stats(decoded, final_sr)
        if signal['blocked']:
            raise ValueError('processed unit failed signal validation')
        record = dict(kind='selected', key=key, unit_id=unit['id'], blocked=False,
            file=final.name, sha256=file_hash(final), seed=selected['seed'], trim=trim,
            signal=signal, asr=selected['asr'], candidate_file=selected['file'],
            source_sha256=unit['source_sha256'], mode=unit['mode'],
            measurement_kind='final_decoded_pcm', sample_rate=final_sr,
            sample_count=len(decoded))
        save_json(done, record)
        return record

    def assemble(self, records):
        import numpy as np
        units = self.plan['units']
        if len(records) != len(units) or any(x.get('blocked') for x in records):
            raise ValueError('cannot assemble blocked/incomplete pilot')
        pieces, measurements = [], []
        for i, (unit, row) in enumerate(zip(units, records)):
            gap = 0 if i == 0 else (self.args.paragraph_gap if unit['paragraph_id'] != units[i-1]['paragraph_id'] else self.args.unit_gap)
            samples = round(gap*self.args.sample_rate)
            wave, sr = self.sf.read(self.directory/'units'/f'{i:04d}'/row['file'], dtype='float32')
            if sr != self.args.sample_rate or len(wave) != row['sample_count']:
                raise ValueError('final measurement changed')
            pieces.extend([np.zeros(samples, dtype='float32'), wave])
            measurements.append(dict(id=unit['id'], source_sha256=unit['source_sha256'],
                measurement_kind='final_decoded_pcm', sample_rate=sr,
                sample_count=len(wave), gap_before_samples=samples))
        data = dict(source_sha256=self.plan['source_sha256'], units=measurements)
        timeline = measured_timeline(self.plan, data)
        wav = self.directory/'pilot.wav'
        self.sf.write(wav, np.concatenate(pieces), self.args.sample_rate, subtype='FLOAT')
        mp3 = self.directory/'pilot.mp3'
        subprocess.run(['ffmpeg','-v','error','-y','-i',str(wav),'-c:a','libmp3lame','-q:a','2',
            '-metadata','title=김보라 — 통합방위법 시험 제작',
            '-metadata','artist=김보라 (AI 합성 음성)',str(mp3)], check=True)
        check = self.directory/'pilot.decoded.wav'
        subprocess.run(['ffmpeg','-v','error','-y','-i',str(mp3),'-ac','1','-ar',str(self.args.sample_rate),
                        '-c:a','pcm_f32le',str(check)],check=True)
        wave, sr = self.sf.read(check, dtype='float32')
        signal = signal_stats(wave, sr)
        if signal['blocked'] or len(wave) != timeline['total_samples']:
            raise ValueError('final MP3 signal/duration differs from measured timeline')
        save_json(self.directory/'measurements.json', data)
        save_json(self.directory/'timeline.json', timeline)
        receipt = dict(schema='kim-bora-pilot-receipt/v1', lesson_id=self.plan['lesson_id'],
            source_sha256=self.plan['source_sha256'], signal=signal,
            mp3_sha256=file_hash(mp3), wav_sha256=file_hash(wav),
            total_units=len(units), total_paragraphs=len(self.plan['paragraphs']),
            total_asr_character_errors=sum(x['asr']['character_errors'] for x in records),
            units_requiring_asr_review=[x['unit_id'] for x in records if x['asr']['review_required']],
            perceptual_review_required=True, sentence_alignment_verified=False,
            status='private_pilot_rendered_not_deployed')
        save_json(self.directory/'receipt.json', receipt)
        print(json.dumps(dict(event='complete', seconds=signal['seconds'], units=len(units),
            asr_review_units=len(receipt['units_requiring_asr_review']))), flush=True)


def render_subset(plan, output_dir, qwen_path, asr_path, unit_indices, *, threads=2,
                  sample_rate=24000, max_new_tokens=900, max_cer=.08,
                  paragraph_gap=.35, unit_gap=.18):
    """Offline wrapper API: returns private subset manifest, never assembles.

    Seal subset.json and each successful row's relative_file. Nonzero ASR
    disagreement remains a review item even if below the blocking threshold.
    """
    assert_offline()
    check_plan(plan)
    indices = list(unit_indices)
    if (not indices or indices != sorted(set(indices)) or
            any(type(i) is not int or not 0 <= i < len(plan['units']) for i in indices)):
        raise ValueError('expected sorted unique in-range unit indices')
    if not (1 <= threads <= 32 and sample_rate in (24000,44100,48000)
            and 100 <= max_new_tokens <= 1500 and 0 <= max_cer <= .2
            and 0 <= paragraph_gap <= 2 and 0 <= unit_gap <= 2):
        raise ValueError('invalid rendering settings')
    args = argparse.Namespace(output=Path(output_dir), model=Path(qwen_path),
        asr=Path(asr_path), threads=threads, sample_rate=sample_rate,
        max_new_tokens=max_new_tokens, max_cer=max_cer,
        paragraph_gap=paragraph_gap, unit_gap=unit_gap)
    if not args.model.is_dir() or not args.asr.is_dir():
        raise ValueError('local models required')
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.output.is_symlink():
        raise ValueError('output symlink rejected')
    expected = dict(source_sha256=plan['source_sha256'], plan_sha256=identity(plan))
    existing = args.output/'plan-identity.json'
    if existing.exists() and json.loads(existing.read_text()) != expected:
        raise ValueError('output directory belongs to a different plan')
    save_json(existing, expected)
    renderer = Renderer(args, plan)
    records = [renderer.render_unit(plan['units'][i],i) for i in indices]
    return export_subset(plan, records, min(indices), max(indices)+1, args.output, indices, renderer.settings)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('plan', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--model', type=Path, required=True)
    p.add_argument('--asr', type=Path, required=True)
    p.add_argument('--threads', type=int, default=4)
    p.add_argument('--sample-rate', type=int, default=24000)
    p.add_argument('--max-new-tokens', type=int, default=900)
    p.add_argument('--max-cer', type=float, default=.08)
    p.add_argument('--paragraph-gap', type=float, default=.35)
    p.add_argument('--unit-gap', type=float, default=.18)
    p.add_argument('--start', '--unit-start', dest='start', type=int, default=0)
    p.add_argument('--stop', '--unit-end', dest='stop', type=int, default=None,
                   help='exclusive upper unit index')
    p.add_argument('--unit-index', type=int, help='render just this unit; overrides start/stop')
    p.add_argument('--no-assemble', action='store_true')
    args = p.parse_args()
    if not (1 <= args.threads <= 32 and args.sample_rate in (24000,44100,48000)
            and 100 <= args.max_new_tokens <= 1500 and 0 <= args.max_cer <= .2
            and 0 <= args.paragraph_gap <= 2 and 0 <= args.unit_gap <= 2):
        p.error('invalid rendering settings')
    os.umask(0o077)
    plan = json.loads(args.plan.read_text())
    check_plan(plan)
    if not args.model.is_dir() or not args.asr.is_dir():
        p.error('local model directories required')
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.output.is_symlink():
        p.error('output symlink rejected')
    existing = args.output/'plan-identity.json'
    expected = dict(source_sha256=plan['source_sha256'], plan_sha256=identity(plan))
    if existing.exists() and json.loads(existing.read_text()) != expected:
        raise ValueError('output directory belongs to a different plan')
    save_json(existing, expected)
    if args.unit_index is not None:
        args.start, args.stop = args.unit_index, args.unit_index + 1
    stop = len(plan['units']) if args.stop is None else args.stop
    if not 0 <= args.start <= stop <= len(plan['units']):
        p.error('invalid unit range')
    renderer = Renderer(args, plan)
    records = [renderer.render_unit(plan['units'][i], i) for i in range(args.start,stop)]
    export_subset(plan, records, args.start, stop, args.output, settings=renderer.settings)
    blocked = [r['unit_id'] for r in records if r.get('blocked')]
    if blocked:
        save_json(args.output/'blocked.json',dict(units=blocked,status='review_required'))
        raise SystemExit(2)
    if not args.no_assemble and args.start == 0 and stop == len(plan['units']):
        renderer.assemble(records)


if __name__ == '__main__':
    main()
