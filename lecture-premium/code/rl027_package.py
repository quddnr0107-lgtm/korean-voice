"""Build a fresh encrypted RL-027 assembly from one verified unit. Never publish."""
import argparse
import base64
import copy
import io
import math
import os
from pathlib import Path
import sys
import tempfile
import zipfile

import numpy as np
import delivery
import lecture_crypto as crypto
import lecture_pack as pack
import rl027_candidate as candidate
from rl027_candidate import LESSON, RATE, TARGET, require
from rl027_candidate_text import validate_donor

sys.path.insert(0, str(candidate.ROOT / 'bora-production'))
import assemble


def load_candidate(directory, key, segment):
    stem = directory / 'RL-027.unit44.candidate.sealed'
    manifest = delivery.loads(delivery.read(Path(str(stem) + '.json'), delivery.MAX_JSON))
    ciphertext = delivery.read(Path(str(stem) + '.bin'), 4 * 1024 * 1024)
    raw, _ = crypto.open_range(key, manifest, ciphertext, expected_id=LESSON,
                               expected_kind='checkpoint', expected_key_id=delivery.KEY_ID)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.infolist()
        require(sorted(f.filename for f in members) == ['candidate.json', 'unit.s16le']
                and all(f.compress_type == zipfile.ZIP_STORED for f in members), 'candidate_schema')
        evidence = delivery.loads(archive.read('candidate.json'))
        pcm = archive.read('unit.s16le')
    summary = evidence['candidate']
    require(evidence['schema'] == candidate.SCHEMA and evidence['lesson_id'] == LESSON
            and evidence['unit_index'] == TARGET and evidence['speaker'] == candidate.SPEAKER
            and evidence['source_descriptor_sha256'] == candidate.check.DESCRIPTOR_DIGEST
            and evidence['spoken_sha256'] == candidate.SPOKEN_SHA and evidence['rate'] == RATE
            and evidence['channels'] == 1 and evidence['pcm_format'] == 's16le', 'candidate_identity')
    require(evidence['original_start_sample'] == segment['start_sample']
            and evidence['original_end_sample'] == segment['end_sample']
            and summary['original_samples'] == segment['end_sample'] - segment['start_sample']
            and 2 * RATE <= summary['candidate_samples'] <= 32 * RATE
            and len(pcm) == 2 * summary['candidate_samples']
            and crypto.digest(pcm) == evidence['pcm_sha256'], 'candidate_timing_or_digest')
    require(summary['accepted'] is True and [row['speed'] for row in summary['checks']] == [1., 1.2]
            and all(row['accepted'] is True and row['words']['accepted'] is True for row in summary['checks']),
            'candidate_quality')
    return evidence, pcm


def set_times(record, begin, end):
    record.update(start_sample=begin, end_sample=end, start_s=begin / RATE, end_s=end / RATE)


def build_pack(original, key, evidence, pcm, work, verify_delivery_audio, *, target=TARGET):
    record = copy.deepcopy(original.record)
    old_audio = original.audio
    require(record['audio_pack_sha256'] == old_audio['pack_sha256'] and
            len(record['audio_parts']) == len(old_audio['parts']), 'old_audio_identity')
    segments = record['segments']
    segment = segments[target]
    begin, end = segment['start_sample'], segment['end_sample']
    sample_count = len(pcm) // 2
    delta = sample_count - (end - begin)
    anchors = [p for p in old_audio['parts'] if p['start_sample'] <= begin and end <= p['end_sample']]
    require(len(anchors) == 1, 'unit_partition')
    changed_index = anchors[0]['part_index']
    set_times(segment, begin, end + delta)
    for later in segments[target + 1:]:
        set_times(later, later['start_sample'] + delta, later['end_sample'] + delta)
    measurements = record['unit_measurements']
    previous = measurements[target]
    require(previous['unit_index'] == target and previous['unit_id'] == segment['unit_id']
            and previous['sample_count'] == end - begin, 'old_unit_identity')
    unit_pcm = np.frombuffer(pcm, dtype='<i2').astype('<f4') / 32768
    unit_stats = assemble._metrics(unit_pcm)
    assemble._require_signal(unit_stats)
    measurements[target] = dict(unit_index=target, unit_id=segment['unit_id'], gap_before_samples=previous['gap_before_samples'],
                                sample_rate=RATE, sample_count=sample_count, measurement_kind='repair_candidate_pcm',
                                input_sha256=evidence['pcm_sha256'], receipt_sha256=assemble.identity(evidence), validation=unit_stats)
    new_parts, sealed_parts, media = [], [], bytearray()
    pack_id = os.urandom(16).hex()
    delivery_checks = None

    def append(part, plaintext):
        require(part['audio_sha256'] == crypto.digest(plaintext) and part['plain_bytes'] == len(plaintext),
                'new_mp3_digest')
        index = len(new_parts)
        part['index'] = index
        header, cipher = pack.seal_part(key, plaintext, LESSON, pack_id, index, part['start_sample'],
                                       part['end_sample'], len(media))
        require(pack.open_part(key, header, cipher, expected_id=LESSON, expected_part_index=index,
                               expected_pack_id=pack_id) == plaintext, 'ciphertext_roundtrip')
        new_parts.append(part)
        sealed_parts.append(header)
        media.extend(cipher)

    for header, part in zip(old_audio['parts'], record['audio_parts']):
        index = header['part_index']
        left, right = header['start_sample'], header['end_sample']
        require(index == part['index'] and left == part['start_sample'] and right == part['end_sample'], 'old_partition')
        offset = header['pack_offset']
        raw = pack.open_part(key, header, original.cipher[offset:offset + header['cipher_bytes']],
                             expected_id=LESSON, expected_part_index=index, expected_pack_id=old_audio['object_id'])
        require(part['audio_sha256'] == crypto.digest(raw) and part['plain_bytes'] == len(raw), 'old_mp3_digest')
        if index != changed_index:
            shift = delta if index > changed_index else 0
            set_times(part, left + shift, right + shift)
            append(part, raw)
            continue
        old_pcm = candidate.decoded(raw)
        require(abs(len(old_pcm) // 2 - (right - left)) <= 1, 'old_decode_length')
        old_pcm = old_pcm[:2 * (right - left)].ljust(2 * (right - left), b'\0')
        replaced = old_pcm[:2 * (begin - left)] + pcm + old_pcm[2 * (end - left):]
        floats = np.frombuffer(replaced, dtype='<i2').astype('<f4') / 32768
        # The existing producer partitions only at measured unit boundaries.
        local_units = [dict(start_sample=max(0, s['start_sample'] - left),
                            end_sample=min(len(floats), s['end_sample'] - left))
                       for s in segments if s['end_sample'] > left and s['start_sample'] < right + delta]
        cuts = assemble._part_boundaries(local_units, len(floats), pack.MAX_PART_SAMPLES)
        for a, b in zip(cuts, cuts[1:]):
            path = work / f'part-{len(new_parts)}.mp3'
            output = assemble._encode_part(floats[a:b].tobytes(), path, b - a, 'ffmpeg')
            set_times(output, left + a, left + b)
            output['sample_rate'] = RATE
            new_mp3 = path.read_bytes()
            if left + a <= begin and end + delta <= left + b:
                decoded = candidate.decoded(new_mp3)
                at = begin - left - a
                clip = decoded[at * 2:(at + sample_count) * 2]
                require(len(clip) == len(pcm), 'encoded_unit_length')
                passed, delivery_checks = verify_delivery_audio(clip)
                require(passed is True, 'encoded_audio_quality')
            append(output, new_mp3)
    require(delivery_checks is not None, 'encoded_unit_not_verified')
    blob = bytes(media)
    audio = pack.make_pack_manifest(LESSON, pack_id, sealed_parts, blob)
    total = audio['total_samples']
    decoded_total = sum(p['decoded_sample_count'] for p in new_parts)
    require(total == record['total_samples'] + delta == segments[-1]['end_sample'] and
            abs(decoded_total - total) <= RATE * .03, 'new_coverage')
    metrics = [p['validation'] for p in new_parts]
    record.update(audio_parts=new_parts, audio_pack_sha256=audio['pack_sha256'], total_samples=total,
                  validation=dict(duration_s=total / RATE, finite=all(p['finite'] for p in metrics),
                                  clipped_samples=sum(p['clipped_samples'] for p in metrics),
                                  peak=max(p['peak'] for p in metrics), max_silence_s=max(p['max_silence_s'] for p in metrics),
                                  rms=math.sqrt(sum(p['energy'] for p in metrics) / decoded_total),
                                  decoded_sample_count=decoded_total, decoded_sample_delta=decoded_total - total))
    record['unit_repair'] = dict(schema='rl027-unit-repair/v1', target_index=target,
                                 baseline_descriptor_sha256=evidence['source_descriptor_sha256'],
                                 original_measurement=previous, original_part_index=changed_index,
                                 original_start_sample=begin, original_end_sample=end, new_end_sample=end + delta,
                                 candidate=evidence, final_mp3_checks=delivery_checks)
    serialized = crypto.enc(record)
    content, private = crypto.seal(key, serialized, LESSON, 'private-metadata', delivery.KEY_ID)
    recovered, _ = crypto.open_range(key, content, private, expected_id=LESSON,
                                     expected_kind='private-metadata', expected_key_id=delivery.KEY_ID)
    require(recovered == serialized, 'metadata_roundtrip')
    descriptor = delivery.check_descriptor(dict(version='integrated-lecture-output/v2', lesson_id=LESSON,
                                                audio=audio, content=content), LESSON)
    return descriptor, blob, private


def one_lesson_index(descriptor_bytes):
    tag = 'bora-fp32-rl027-unit44-v1-20261119'
    descriptor_url = ('https://github.com/quddnr0107-lgtm/korean-voice/releases/download/'
                      + tag + '/' + delivery.descriptor_name(LESSON))
    return crypto.enc(dict(version='integrated-lecture-index/v2', release_tag=tag, count=1,
                           lessons=[dict(id=LESSON, descriptor_url=descriptor_url,
                                         sha256=crypto.digest(descriptor_bytes))]))


def stage(source, sealed_candidate, destination, model):
    candidate.guarded()
    key = base64.b64decode(os.environ.pop('ADMIN_LAW_PREMIUM_MEDIA_KEY_B64'), validate=True)
    original = candidate.Original(source, key)
    candidate.target_plan(original.record)
    validate_donor(original.record)
    evidence, pcm = load_candidate(sealed_candidate, key, original.record['segments'][TARGET])
    recognizer = candidate.load_recognizer(model)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    require(not destination.exists(), 'destination_exists')
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix='.rl027-') as stage_dir:
        work = Path(stage_dir)
        descriptor, audio, private = build_pack(original, key, evidence, pcm, work,
            lambda clip: candidate.score_candidate(clip, recognizer))
        out = work / 'sealed'
        out.mkdir(mode=0o700)
        for typ, cipher in [('audio', audio), ('content', private)]:
            (out / delivery.media_name(LESSON, typ, descriptor[typ])).write_bytes(cipher)
        raw = crypto.enc(descriptor)
        (out / delivery.descriptor_name(LESSON)).write_bytes(raw)
        # Verify the original transport's exact encrypted triple before adding its public index.
        delivery.output_gate(out, LESSON)
        index = one_lesson_index(raw)
        (out / 'index.json').write_bytes(index)
        require(sorted(x.name for x in out.iterdir()) == sorted([
            delivery.media_name(LESSON, typ, descriptor[typ]) for typ in ('audio', 'content')
        ] + [delivery.descriptor_name(LESSON), 'index.json']), 'pack_output_allowlist')
        require(not destination.exists(), 'destination_exists')
        out.rename(destination)
    # These sample landmarks let the catalog migration preserve old listening
    # ranges and resume positions without disclosing the private manuscript.
    print(crypto.enc(dict(lesson=LESSON, descriptor_sha256=crypto.digest(raw), index_sha256=crypto.digest(index),
                          duration_s=descriptor['audio']['total_samples'] / RATE,
                          source_descriptor_sha256=evidence['source_descriptor_sha256'],
                          original_start_sample=evidence['original_start_sample'],
                          original_end_sample=evidence['original_end_sample'],
                          replacement_end_sample=evidence['original_start_sample'] + len(pcm) // 2,
                          total_samples=descriptor['audio']['total_samples'], sample_rate=RATE)).decode())


if __name__ == '__main__':
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    for name in ('source', 'sealed_candidate', 'destination', 'model'):
        parser.add_argument(name, type=Path)
    try:
        stage(**vars(parser.parse_args()))
    except Exception as err:
        print('RL-027 packaging failed:', type(err).__name__, file=sys.stderr)
        raise SystemExit(1) from None
