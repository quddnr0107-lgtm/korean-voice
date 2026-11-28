"""Render only the source-bound directive repairs in the original protected renderer."""
import argparse
import base64
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import zipfile

import corrected_plan
import old_media
from batch3 import config as forms


SOURCE_COMMIT = '367750f745f0d1873290b9885f790f5484be3cd7'
ENGINE = 'db2081bf68cdc3e4cd6a73a3a719b68f58cb675f'
TRANSPORT = 'b4d718341fa0599f129e025ddb7a540cffe48a67'
RECIPE = 'd3d35666712d276c0fa60bb5c5b1f7ab7d11c7042d490c95bb6fb8846682502d'
SETTINGS = '2fabab4a10c67164403f759fc12b9af489a4eeb948599d4657e014ec75c96d13'
KEY_ID = 'exam80-dr-table-revision-v1'
SOURCE_FILES = {
    'DR-052': ('6a662dc2524334920d92bfe2d50ab68076799c922242f9abfa6c4b1a42a9db96',
               '53853f6a740b3db05a5124ba0c48b36176ceced7fd5782fa7866c57a545542e9'),
    'DR-054': ('6f6f0f9c09fd32b4b1900fcbbdf11f59bfa886880e420b6bf54e31812b8d7e5a',
               '2cb376a69315e5d79688770c922be8b840325baa3fb4ee14a9ec0cd0868ce031'),
    'DR-038': ('cd2b20afe540b780582a9087bf6877800c5c477b88e68a32daa03856645659c0',
               '6cf7c384362c909a4ff0e48fae50e17382cef4689695de2379e386e84bd77ec0'),
    'DR-047': ('94949b3e77ac8c4986587005d5d809099f8b2acd0fb94c7fd7b341e311690404',
               '5d8eb9a58356938246059659e31f42ea48d9339858d2f8d5ec53648e5541668e'),
}
SOURCE_FILES.update(forms.SOURCE_FILES)
BATCH2_COMMITS = {'DR-038': 'f543f423a92843a7e47b5dfeab9c428d0553716f',
                  'DR-047': '6ed18c9353a023b7b5287867a3ccefeef0c583a8'}


class RevisionError(Exception):
    pass


def require(value, code):
    if not value:
        raise RevisionError(code)


def current_commit(folder):
    return subprocess.check_output(['git', '-C', str(folder), 'rev-parse', 'HEAD'],
                                   stderr=subprocess.DEVNULL, text=True).strip()


def encode_paragraph(pcm, path, repair):
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-f', 'f32le', '-ar', '44100',
                    '-ac', '1', '-i', 'pipe:0', '-map_metadata', '-1', '-c:a', 'libmp3lame',
                    '-b:a', '48k', '-ar', '44100', '-ac', '1', '-write_xing', '1', str(path)],
                   input=pcm, capture_output=True, check=True, timeout=120)
    decode = ['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-i', str(path),
              '-map', '0:a:0', '-f', 's16le', '-']
    expected = len(pcm) // 4
    count = len(subprocess.check_output(decode, stderr=subprocess.DEVNULL, timeout=120)) // 2
    if 1 <= count - expected <= 46 and 1 <= expected % 1152 <= 46:
        path.write_bytes(repair(path.read_bytes(), expected, count))
        count = len(subprocess.check_output(decode, stderr=subprocess.DEVNULL, timeout=120)) // 2
    require(count == expected and count > 0, 'encoded_samples')
    return count


def render_paragraph(runtime, assembly, renderer, work, row, recipe, repair):
    number, start, end, units = row
    checks, pcm = [], bytearray()
    previous = None
    for idx, unit in units:
        folder = work / f'u{idx:04}'
        signal.alarm(recipe['unit_timeout_seconds'])
        try:
            receipt = renderer.render_unit(unit, folder)
        finally:
            signal.alarm(0)
        require(receipt.get('status') == 'passed' and receipt.get('natural_eos') is True
                and receipt.get('automatic_checks_passed') is True and receipt.get('production_eligible') is True
                and receipt.get('blocked') is False and receipt.get('source_sha256') == unit['source_sha256']
                and receipt.get('spoken_sha256') == unit['spoken_sha256'] and receipt.get('file') == 'final.wav'
                and receipt.get('render_settings_sha256') == SETTINGS
                and receipt.get('asr') == {'status': 'not_run', 'performed': False}, 'unit_gate')
        data, metrics = assembly._input_pcm(folder / 'final.wav', receipt, 'ffmpeg', 'ffprobe',
                                            compact_pauses=True)
        if previous is not None:
            gap = (recipe['paragraph_gap_ms'] if previous != unit['paragraph_id'] else recipe['unit_gap_ms'])
            pcm.extend(bytes(4 * round(gap * 44100 / 1000)))
        previous = unit['paragraph_id']
        unit_start = len(pcm) // 4
        pcm.extend(data)
        checks.append(dict(unit_index=idx, source_sha256=unit['source_sha256'], spoken_sha256=unit['spoken_sha256'],
                           receipt_sha256=runtime.identity(receipt), wav_sha256=receipt['sha256'],
                           natural_eos=True, automatic_checks_passed=True, mode=unit['mode'],
                           source_start_cp=unit['start_cp'], source_end_cp=unit['end_cp'],
                           start_sample=unit_start, end_sample=len(pcm)//4, signal=metrics))
    name = f'p{number:03}.mp3'
    count = encode_paragraph(pcm, work / name, repair)
    return dict(paragraph=number, file=name, sha256=runtime.sha((work / name).read_bytes()),
                decoded_samples=count, source_start_cp=start, source_end_cp=end, unit_checks=checks)


def run(args):
    os.umask(0o077)
    engine_commit = forms.ENGINES.get(args.lesson, ENGINE)
    require(current_commit(args.engine) == engine_commit and current_commit(args.transport) == TRANSPORT, 'checkout_pins')
    require(not args.output.exists(), 'output_exists')
    ident = args.lesson
    engine = args.engine / 'bora-production'
    sys.path.insert(0, str(engine))
    import runtime
    import assemble
    import planning
    sys.path.append(str(args.transport / 'bora-production'))
    from mp3_gapless48 import repair
    campaign, recipe, request, eligible = runtime.config('single_pass')
    require(runtime.identity(recipe) == RECIPE and ident in eligible and request['course'] == 'DR',
            'original_campaign_authorization')
    env_hash, audio_hash = SOURCE_FILES[ident]
    folder = Path(__file__).resolve().parent
    trigger_fields = dict(schema='exam80-directive-private-corrections/v1', engine_commit=ENGINE)
    if ident in forms.COMMITS:
        from batch3 import corrected_plan as plan_scope
        media_scope = old_media
        folder = folder / "batch3"
        source_commit = forms.COMMITS[ident]
        trigger_fields = forms.request()
    elif ident in BATCH2_COMMITS:
        from batch2 import corrected_plan as plan_scope, old_media as media_scope
        folder = folder / 'batch2'
        source_commit = BATCH2_COMMITS[ident]
        trigger_fields.update(lessons=list(BATCH2_COMMITS), private_source_commits=BATCH2_COMMITS)
    else:
        plan_scope, media_scope = corrected_plan, old_media
        source_commit = SOURCE_COMMIT
        trigger_fields.update(lessons=['DR-052','DR-054'], private_source_commit=SOURCE_COMMIT)
    require(runtime.read_json(folder / 'request.json') == trigger_fields, 'revision_request_scope')
    envelope_file = folder / (ident + '.sealed.json')
    require(runtime.sha(envelope_file.read_bytes()) == env_hash, 'revised_envelope_pin')
    envelope = runtime.read_json(envelope_file)
    source_key, media_key = runtime.protected_keys(media=True, profile='single_pass')
    with os.fdopen(os.dup(1), 'w', buffering=1) as progress, runtime.quiet():
        old_plan, _ = runtime.source_plan(ident, campaign, source_key)
        raw = runtime.crypto.open_input(source_key, runtime.read_json(engine/'receiver-public.json'),
            runtime.read_json(engine/'receiver-wrapped.json'), envelope['record'],
            base64.b64decode(envelope['cipher_b64'], validate=True), ident)
        del source_key
        source = raw.decode('utf-8', errors='strict')
        plan = runtime.course_pronunciation('single_pass', 'DR').apply_to_plan(planning.build_plan(ident, source))
        selected = plan_scope.select(plan, old_plan['source'], source, ident)
        old_receipt = media_scope.open_receipt(args.transport / 'bora-production', args.old_media, ident, media_key)
        require(old_receipt['lesson_id'] == ident and old_receipt['recipe_sha256'] == RECIPE
                and old_receipt['render_settings_sha256'] == SETTINGS, 'original_media_receipt')
        signal.signal(signal.SIGALRM, runtime.alarm)
        with tempfile.TemporaryDirectory(prefix='directive-revision-', dir=os.environ['RUNNER_TEMP']) as temp:
            work = Path(temp)
            renderer = runtime.make_renderer(recipe, 'single_pass', course='DR')
            try:
                require(renderer.settings_sha256 == SETTINGS, 'renderer_identity')
                segments = []
                for row in selected:
                    segment = render_paragraph(runtime, assemble, renderer, work, row, recipe, repair)
                    if ident in BATCH2_COMMITS or ident in forms.COMMITS:
                        segment.update(plan_scope.extra(ident, row[0]))
                    segments.append(segment)
                    print(ident, 'verified paragraphs', len(segments), 'of', len(selected), file=progress)
            finally:
                runtime.close_renderer(renderer)
            index = dict(schema='exam80-dr-correction-audio/v1', lesson_id=ident,
                         private_source_commit=source_commit, engine_commit=engine_commit, transport_commit=TRANSPORT,
                         source_sha256=plan['source_sha256'], original_source_sha256=plan_scope.PINS[ident][0],
                         original_audio_sha256=audio_hash, recipe_sha256=RECIPE, render_settings_sha256=SETTINGS,
                         original_receipt=old_receipt, segments=segments, asr_performed=False)
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_STORED) as archive:
                archive.writestr('index.json', runtime.enc(index))
                for seg in segments:
                    archive.write(work / seg['file'], arcname=seg['file'])
            clear = buffer.getvalue()
            manifest, encrypted = runtime.crypto.seal(media_key, clear, ident, 'checkpoint', KEY_ID)
            check, _ = runtime.crypto.open_range(media_key, manifest, encrypted, expected_id=ident,
                                                 expected_kind='checkpoint', expected_key_id=KEY_ID)
            require(check == clear, 'recovery_roundtrip')
            args.output.mkdir(mode=0o700)
            runtime.atomic(args.output / 'manifest.json', runtime.enc(manifest))
            runtime.atomic(args.output / 'payload.bin', encrypted)
    print(ident + ' corrected paragraphs independently encoded, verified, sealed and recovered.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lesson', choices=SOURCE_FILES, required=True)
    for name in ('engine','transport','old-media','output'):
        parser.add_argument('--' + name, type=Path, required=True)
    try:
        run(parser.parse_args())
    except RevisionError as error:
        raise SystemExit('Correction stopped: ' + str(error)) from None
    except Exception as error:
        raise SystemExit('Correction stopped: ' + type(error).__name__) from None
