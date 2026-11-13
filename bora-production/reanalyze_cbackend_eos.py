"""Append-only EOS reanalysis of the already downloaded public run 43377295943.

No model, synthesis, network, original-artifact changes or production promotion.
Verify original WAV hashes and PCM frame coverage before applying the log parser.
"""
import argparse
import array
import copy
import hashlib
import json
from pathlib import Path
import sys
import wave

import cbackend_sohee_benchmark as b

SOURCE_RUN = 43377295943


def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def reanalyze(directory):
    directory = Path(directory).resolve()
    original_path = directory / 'summary.json'
    b.require(original_path.is_file() and not original_path.is_symlink())
    original = json.loads(original_path.read_text())
    b.require(original.get('schema') == 'cbackend-sohee-public-cpu-benchmark/v1'
        and original.get('source_revision') == b.SOURCE_SHA
        and original.get('model_revision') == b.MODEL_REVISION
        and original.get('public_fixtures') == list(b.FIXTURES))
    b.summarize(original['rows'])  # coverage and numeric-timing integrity
    rows = copy.deepcopy(original['rows'])
    artifacts = {'summary.json': digest(original_path)}
    old_eos = []
    for row in rows:
        i = row['fixture']
        wav = directory / f'cbackend-sohee-{i}.wav'
        log = directory / f'cbackend-sohee-{i}.log'
        b.require(wav.is_file() and log.is_file()
            and not wav.is_symlink() and not log.is_symlink()
            and digest(wav) == row['wav_sha256'])
        artifacts[wav.name] = digest(wav)
        artifacts[log.name] = digest(log)
        old_eos.append({k: row.get(k) for k in
            ('fixture', 'eos_frame', 'natural_eos_observed', 'eos_assistance_enabled')})
        evidence = b.eos_evidence(log.read_text())
        b.require(evidence['natural_eos_observed'])
        with wave.open(str(wav), 'rb') as w:
            b.require(w.getnchannels() == 1 and w.getsampwidth() == 2
                and w.getframerate() == 24000 and w.getcomptype() == 'NONE')
            frames = w.getnframes()
            pcm = array.array('h', w.readframes(frames))
        if sys.byteorder != 'little':
            pcm.byteswap()
        b.require(frames == evidence['generated_frames'] * 1920
            and abs(frames / 24000 - row['audio_seconds']) < 1e-6
            and len(pcm) == frames and max(abs(x) for x in pcm) < 32767
            and sum(x*x for x in pcm) / frames > (32768e-5)**2)
        row.update(evidence, decoded_pcm_frame_coverage_verified=True)
    report = dict(original)
    report.update(b.summarize(rows))
    report['reanalysis'] = dict(
        schema='public-cbackend-eos-reanalysis/v1', source_run_id=SOURCE_RUN,
        source_artifact_files_sha256=artifacts,
        original_complete_natural_eos_and_signal=original['complete_natural_eos_and_signal'],
        original_eos_projection=old_eos,
        parser_source_sha256=digest(Path(b.__file__)),
        helper_source_sha256=digest(Path(__file__)),
        audio_resynthesized=False, original_artifact_modified=False,
        original_process_times_preserved=True, original_wav_hashes_verified=True,
        source_binding_note='Reanalysis of locally materialized GitHub public run 43377295943; file hashes preserve the exact downloaded inputs.')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact-directory', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = reanalyze(args.artifact_directory)
    output = Path(args.output)
    b.require(not output.exists() and not output.is_symlink()
        and output.resolve().parent != Path(args.artifact_directory).resolve())
    with output.open('x') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print(json.dumps({key: result[key] for key in
        ('schema', 'complete_natural_eos_and_signal', 'total_process_wall_seconds',
         'total_audio_seconds', 'process_seconds_per_audio_second', 'production_eligible')}, sort_keys=True))


if __name__ == '__main__': main()
