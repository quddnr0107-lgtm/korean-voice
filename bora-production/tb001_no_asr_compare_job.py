"""Run one bounded group of the owner's listen-and-time comparison."""
import argparse
import io
import json
import os
from pathlib import Path
import tempfile
import time
import zipfile

import tb001_no_asr_compare as compare
import listening_preview as envelope


HERE = Path(__file__).resolve().parent
REQUEST = HERE / 'tb001-no-asr-compare-request.json'
SCHEMA = 'bora-tb001-no-asr-comparison/v1'


def recipient_request(request, group):
    return dict(schema=envelope.SCHEMA, request_id=request['request_id'] + f'-g{group:02d}',
                purpose='owner-only-listening-review', pin=compare.full.PIN, max_clips=3,
                recipient_spki_der_b64=request['recipient_spki_der_b64'],
                recipient_spki_sha256=request['recipient_spki_sha256'],
                synthesis=False, promotion=False, public_plaintext_export=False)


def validate(request):
    expected = dict(schema=SCHEMA, request_id='tb001-five-minute-no-asr-20261114-01',
                    pin=compare.full.PIN, groups=[list(g) for g in compare.GROUPS],
                    asr=False, production_eligible=False,
                    recipient_spki_der_b64=request['recipient_spki_der_b64'],
                    recipient_spki_sha256=request['recipient_spki_sha256'])
    compare.require(request == expected and request['asr'] is False
                    and request['production_eligible'] is False)
    envelope.recipient(recipient_request(request, 1))


def package(request, group, rows, clips):
    child = recipient_request(request, group)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_STORED) as archive:
        for clip, wav in clips:
            archive.write(wav, clip['wav_file'])
        archive.writestr('preview.json', json.dumps(dict(schema=SCHEMA, group=group,
            request_id=child['request_id'], pin=compare.full.PIN,
            synthesis_started=True, asr_performed=False, production_eligible=False,
            clips=[clip for clip, _ in clips], rows=rows), ensure_ascii=False))
    encrypted = envelope.encrypt_payload(buffer.getvalue(), child, clip_count=len(clips))
    encrypted['synthesis_started'] = True
    return encrypted


def render(request, directory, model_dir, group, output):
    started = time.perf_counter()
    output = Path(output)
    compare.require(not output.exists() and output.parent.is_dir())
    rows, clips = [], []
    with tempfile.TemporaryDirectory(prefix='bora-private-five-minute-', dir=os.environ['RUNNER_TEMP']) as temp:
        temp = Path(temp)
        inputs = compare.load_private_inputs(directory, temp / 'restored', group)
        model, torch, soundfile, setup = compare.load_model(
            model_dir, expected_assets=inputs[0][2]['old_model_assets_sha256'])
        for index, unit, baseline in inputs:
            wav, row = compare.render_unit(model, torch, soundfile, index, unit, baseline, temp)
            rows.append(row)
            clips.append((dict(unit_index=index, expected_spoken=unit['spoken'],
                               wav_file=wav.name, wav_sha256=row['wav_sha256']), wav))
            print(json.dumps(dict(status='unit_generated', unit_index=index,
                                  seconds=row['new_tts_seconds'])), flush=True)
        compare.require(tuple(row['unit_index'] for row in rows) == compare.group_indices(group))
        encrypted = package(request, group, rows, clips)
        output.mkdir(mode=0o700)
        compare.full.d.dump(output / 'owner-envelope.json', encrypted)
    summary = dict(schema=SCHEMA, status='comparison_group_complete', group=group,
                   source_run=compare.full.PIN['run_id'], rows=rows, model_setup=setup,
                   worker_elapsed_seconds=time.perf_counter() - started,
                   asr_performed=False, original_checkpoint_written=False,
                   production_eligible=False)
    compare.full.d.dump(output / 'timing.json', summary)
    print(json.dumps(dict(status=summary['status'], group=group, rendered=len(rows),
                          elapsed_seconds=summary['worker_elapsed_seconds'])), flush=True)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('validate', 'download', 'render'))
    parser.add_argument('--directory')
    parser.add_argument('--model-dir')
    parser.add_argument('--group', type=int)
    parser.add_argument('--output')
    args = parser.parse_args()
    request = json.loads(REQUEST.read_text())
    validate(request)
    if args.mode == 'download':
        compare.download(args.model_dir)
    elif args.mode == 'render':
        render(request, args.directory, args.model_dir, args.group, args.output)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Runtime errors can contain manuscript details. Use only fixed comparison codes.
        reason = str(exc)
        if not (reason.startswith('tb001_no_asr_') and reason.isascii()
                and reason.replace('_', '').isalnum() and len(reason) < 100):
            reason = 'comparison_operation_failed'
        print(json.dumps(dict(status='failed', reason=reason, exception_type=type(exc).__name__)), flush=True)
        raise SystemExit(1) from None
