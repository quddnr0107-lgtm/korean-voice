"""Make an encrypted owner A/B preview for three adjacent TB-010 units."""

import base64
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import wave
import zipfile

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import fp32_renderer as fp32
import runtime
from single_pass_renderer import SinglePassRenderer


LESSON = 'TB-010'
WORKFLOW = 'tb010-prosody-preview.yml'
CONTEXT = b'owner-prosody-preview/TB-010/v1'
SELECTED = {
    'TB-010:p0025:u000': '9f1aee7c3fff173f646817179d7de724553b24819d649acfab7810670192bca4',
    'TB-010:p0026:u000': 'e73a87c534de4dcbfc18ca5c50184b06cb0394477672257477b70445ae63898d',
    'TB-010:p0027:u000': '240a315b647111012839d560c3a132e3fa49c585e5ab416239bf69b84e2a80d0',
}
ASSETS = {
    'TB-010.descriptor.json': 'e2243168044a3b1d3e5f3643c4ee0a1482025e6c2b461cb564b9c8d3c2fd3465',
    'TB-010.audio.296eb238d6a5123f39997e666436ecec.bin': '86ff76ada78504757279fe1bbc24d0b331d15b637d974f494fc105932bed5fea',
    'TB-010.content.c173bdd0c0653a47defadae1fed44cae.bin': '8a0d9d34ea32ae5731199128ca8af10c5ba776202921763bd77d97def3813fee',
}
STYLE = ('또렷하고 침착하게 설명합니다. 한숨, 들리는 호흡, 탄식, 신음 없이 '
         '말하고, 끝 음절까지 목소리의 힘과 속도를 고르게 유지합니다. '
         '조문과 해설을 자연스럽게 읽되 과장하거나 문장을 덧붙이지 않습니다.')
RECIPIENT = b'''-----BEGIN PUBLIC KEY-----
MIIBojANBgkqhkiG9w0BAQEFAAOCAY8AMIIBigKCAYEAi6+roIXpOpCwp415fvOv
Gly86G2Oz4Bs3nnhffrm5sgN9M0yejoCa6yzHlmqGv8vYETmDQTgMum/uV425xgM
ves6wsFelhtnOSzCoq+xbIVA+kPrte2XrJT9gEsAzen1MOIxIasXnoFk7v/lP/h7
WqHVcC0l7rIQZK6r/TYMqThKhpCIU5TH0TIM2w3S4ee6KuiYsGJYu/2Vumh9pGtA
gTVD5S5LJsSh8g5JVV0ASp/2Cd8BtHL4VOGNSvgj/WBxSFzjgzGWRaU1/srKY+AT
ZkURNF70+GNiLw3Lx972tZr9ai55ubEWKO1Zr3MB1MyVImqNV0oMNTISxE9GOUgr
eWSmbyr5Jaa9753pd1qcukPAAWgb7nI91i8RvEji6McUHDlr4CiPUHsWA0L8aKJU
DgVZP2dlKFr1ObjmNlJA0AcgkSTzS+Nh91pKP6uctudPDMe6yky11mGB/oiJ3nc7
u3fgR0GFtBj6kVcX+ZJ4UZbg7rz7N8D8rPvoMKZYiUuFAgMBAAE=
-----END PUBLIC KEY-----'''


def select_rows(rows, id_key, hash_of):
    selected = [row for row in rows if row[id_key] in SELECTED]
    if [(row[id_key], hash_of(row)) for row in selected] != list(SELECTED.items()):
        raise ValueError('preview_selection_mismatch')
    return selected


def wav_bytes(pcm, rate):
    result = io.BytesIO()
    with wave.open(result, 'wb') as output:
        output.setparams((1, 2, rate, 0, 'NONE', ''))
        output.writeframes(pcm)
    return result.getvalue()


def original_clip(directory, key):
    verified = {}
    for name, digest in ASSETS.items():
        content = (directory / name).read_bytes()
        if runtime.sha(content) != digest:
            raise ValueError('preview_asset_pin')
        verified[name] = content
    descriptor = json.loads(verified['TB-010.descriptor.json'])
    audio, meta = descriptor['audio'], descriptor['content']
    if descriptor['lesson_id'] != LESSON:
        raise ValueError('preview_lesson_identity')
    pack = verified[f'{LESSON}.audio.{audio["object_id"]}.bin']
    runtime.validate_pack(audio, pack)
    metadata, _ = runtime.crypto.open_range(
        key, meta, verified[f'{LESSON}.content.{meta["object_id"]}.bin'],
        expected_id=LESSON, expected_kind='private-metadata', expected_key_id=runtime.KEY_ID,
    )
    metadata = json.loads(metadata)
    if metadata['id'] != LESSON or metadata['audio_pack_sha256'] != audio['pack_sha256']:
        raise ValueError('preview_media_identity')
    rows = select_rows(metadata['segments'], 'unit_id', lambda row: runtime.sha(row['text'].encode()))
    first, last = rows[0]['start_sample'], rows[-1]['end_sample']
    rate = audio['sample_rate']
    if not 0 <= first < last <= audio['total_samples'] or last - first > 90 * rate:
        raise ValueError('preview_clip_bounds')
    buffer = bytearray()
    for part in audio['parts']:
        begin = max(first, part['start_sample'])
        end = min(last, part['end_sample'])
        if begin >= end:
            continue
        offset = part['pack_offset']
        mp3 = runtime.open_part(
            key, part, pack[offset:offset + part['cipher_bytes']], expected_id=LESSON,
            expected_part_index=part['part_index'], expected_pack_id=audio['object_id'],
        )
        pcm = subprocess.run(
            ['ffmpeg', '-v', 'error', '-f', 'mp3', '-i', 'pipe:0', '-ac', '1',
             '-ar', str(rate), '-f', 's16le', 'pipe:1'],
            input=mp3, capture_output=True, check=True, timeout=30,
        ).stdout
        left = 2 * (begin - part['start_sample'])
        right = 2 * (end - part['start_sample'])
        expected = 2 * (part['end_sample'] - part['start_sample'])
        if abs(len(pcm) - expected) > 2304:
            raise ValueError('preview_original_part_length')
        buffer.extend(pcm[:expected].ljust(expected, b'\0')[left:right])
    if len(buffer) != 2 * (last - first):
        raise ValueError('preview_original_incomplete')
    return wav_bytes(buffer, rate), [{'id': row['unit_id'], 'start_s': row['start_s'],
                                     'end_s': row['end_s']} for row in rows]


def seal(archive):
    recipient = serialization.load_pem_public_key(RECIPIENT)
    key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    wrapped = recipient.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                                                 algorithm=hashes.SHA256(), label=CONTEXT))
    return wrapped + nonce + AESGCM(key).encrypt(nonce, archive, CONTEXT)


def run(directory, model, output):
    expected_ref = f'refs/heads/{runtime.BRANCH}'
    expected_workflow = f'{runtime.REPO}/.github/workflows/{WORKFLOW}@{expected_ref}'
    if (os.environ.get('GITHUB_REPOSITORY'), os.environ.get('GITHUB_REF'),
            os.environ.get('GITHUB_WORKFLOW_REF'), os.environ.get('GITHUB_EVENT_NAME')) != (
                runtime.REPO, expected_ref, expected_workflow, 'push'):
        raise ValueError('preview_workflow_context')
    source_key = base64.b64decode(os.environ.pop('ADMIN_LAW_PREMIUM_SOURCE_KEY_B64'), validate=True)
    media_key = base64.b64decode(os.environ.pop('ADMIN_LAW_PREMIUM_MEDIA_KEY_B64'), validate=True)
    fp32.require_guard()
    if len(source_key) != 32 or len(media_key) != 32 or source_key == media_key:
        raise ValueError('preview_key_scope')
    original, timing = original_clip(directory, media_key)
    plan, _ = runtime.source_plan(LESSON, runtime.read_json(runtime.HERE / 'campaign.json'), source_key)
    del source_key, media_key
    units = select_rows(plan['units'], 'id', lambda row: row['source_sha256'])
    renderer = SinglePassRenderer(model)
    renderer.seeds = (fp32.SEEDS[1],)
    renderer.settings['seeds'] = list(renderer.seeds)
    for mode in {unit['mode'] for unit in units}:
        renderer.settings['styles'][mode] += ' ' + STYLE
    renderer.settings['owner_preview_only'] = True
    renderer.settings_sha256 = fp32.identity(renderer.settings)
    bundle = io.BytesIO()
    results = []
    with tempfile.TemporaryDirectory(prefix='tb010-prosody-', dir=os.environ['RUNNER_TEMP']) as tmp:
        with zipfile.ZipFile(bundle, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('A-published.wav', original)
            for index, unit in enumerate(units, 1):
                out = Path(tmp) / str(index)
                receipt = renderer.render_unit(unit, out)
                status = receipt['status']
                results.append({'unit': unit['id'], 'status': status})
                if status == 'passed':
                    archive.write(out / 'final.wav', f'B-candidate-{index}.wav')
            archive.writestr('comparison.json', json.dumps({'original_segments': timing, 'candidates': results}))
    runtime.atomic(output, seal(bundle.getvalue()))
    return sum(row['status'] == 'passed' for row in results)


if __name__ == '__main__':
    try:
        with runtime.quiet():
            count = run(*(Path(arg) for arg in sys.argv[1:]))
    except Exception as exc:
        # Keep exceptions containing protected source or native model output out of logs.
        safe = {'preview_selection_mismatch', 'preview_asset_pin', 'preview_media_identity'}
        reason = str(exc) if type(exc) is ValueError and str(exc) in safe else 'preview_failed'
        print(json.dumps({'status': 'failed', 'reason': reason}), flush=True)
        raise SystemExit(1) from None
    print(json.dumps({'status': 'owner_preview_encrypted', 'candidate_count': count}), flush=True)
