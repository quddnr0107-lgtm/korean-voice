"""Owner-only listening preview of saved blocked audio; never production output.

The remote job receives only the owner's temporary RSA public key. Source and
media keys stay in the existing offline namespace. Only a fresh hybrid-encrypted
envelope leaves it; its plaintext contains at most three unchanged review WAVs
and the text needed to compare them. No synthesis, promotion or key export.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import socket
import struct
import tempfile
import zipfile

import tb001_full_diagnostic as full

SCHEMA = 'bora-owner-listening-preview/v1'
PAYLOAD_SCHEMA = 'bora-owner-listening-payload/v1'
WORKFLOW = full.d.REPO+'/.github/workflows/bora-listening-preview.yml@refs/heads/'+full.d.BRANCH
REQUEST = Path(__file__).with_name('listening-preview-request.json')
MAX_CLIPS = 3
MAX_WAV_BYTES = 16*1024*1024
MAX_PLAIN_BYTES = 32*1024*1024
MAX_JSON_BYTES = 128*1024
PRIVATE_KEYS = ('ADMIN_LAW_PREMIUM_SOURCE_KEY_B64', 'ADMIN_LAW_PREMIUM_MEDIA_KEY_B64')
HASH = re.compile(r'[a-f0-9]{64}')


def require(ok):
    if not ok:
        raise ValueError('owner_listening_preview_validation_failed')


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(',', ':'), allow_nan=False).encode('ascii')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def recipient(request):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    require(isinstance(request, dict) and set(request) == {
        'schema', 'request_id', 'purpose', 'pin', 'max_clips',
        'recipient_spki_der_b64', 'recipient_spki_sha256',
        'synthesis', 'promotion', 'public_plaintext_export'})
    require(request['schema'] == SCHEMA and request['purpose'] == 'owner-only-listening-review'
            and request['pin'] == full.PIN and type(request['max_clips']) is int
            and request['max_clips'] == MAX_CLIPS and request['synthesis'] is False
            and request['promotion'] is False and request['public_plaintext_export'] is False
            and isinstance(request['request_id'], str)
            and re.fullmatch(r'[a-z0-9-]{1,80}', request['request_id'])
            and isinstance(request['recipient_spki_sha256'], str)
            and HASH.fullmatch(request['recipient_spki_sha256'])
            and isinstance(request['recipient_spki_der_b64'], str)
            and 0 < len(request['recipient_spki_der_b64']) < 8192)
    der = base64.b64decode(request['recipient_spki_der_b64'], validate=True)
    require(digest(der) == request['recipient_spki_sha256'])
    key = serialization.load_der_public_key(der)
    require(isinstance(key, rsa.RSAPublicKey) and 3072 <= key.key_size <= 8192
            and key.public_numbers().e == 65537)
    require(key.public_bytes(serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo) == der)
    return key


def check_request():
    request = json.loads(REQUEST.read_text())
    recipient(request)
    return request


def check_context(env, uid, euid):
    require(env.get('GITHUB_REPOSITORY') == full.d.REPO
            and env.get('GITHUB_REF') == 'refs/heads/'+full.d.BRANCH
            and env.get('GITHUB_EVENT_NAME') == 'push'
            and env.get('GITHUB_WORKFLOW_REF') == WORKFLOW
            and uid == euid and uid != 0)


def wav_info(data):
    """Inspect original mono 24 kHz FLOAT/PCM WAV, without decoding or editing."""
    require(isinstance(data, bytes) and 44 <= len(data) <= MAX_WAV_BYTES
            and data[:4] == b'RIFF' and data[8:12] == b'WAVE'
            and struct.unpack_from('<I', data, 4)[0]+8 == len(data))
    offset, fmt, pcm = 12, None, None
    while offset+8 <= len(data):
        kind, size = struct.unpack_from('<4sI', data, offset)
        begin, end = offset+8, offset+8+size
        require(end <= len(data))
        chunk = data[begin:end]
        if kind == b'fmt ':
            require(fmt is None and size >= 16)
            fmt = struct.unpack_from('<HHIIHH', chunk)
            code, channels, rate, byte_rate, align, bits = fmt
            if code == 65534:
                require(size >= 40 and struct.unpack_from('<H', chunk, 16)[0] >= 22)
                code = struct.unpack_from('<I', chunk, 24)[0]
            require(code in (1, 3) and channels == 1 and rate == 24000
                    and bits in (16, 24, 32) and (code != 3 or bits == 32)
                    and align == bits//8 and byte_rate == rate*align)
        elif kind == b'data':
            require(pcm is None and size > 0)
            pcm = chunk
        offset = end+(size % 2)
    require(offset == len(data) and fmt is not None and pcm is not None)
    align = fmt[4]
    require(len(pcm) % align == 0)
    return dict(sample_rate=24000, sample_count=len(pcm)//align,
                seconds=(len(pcm)//align)/24000,
                pcm_sha256=digest(pcm), original_wav_sha256=digest(data))


def bounded_text(value, *, allow_empty=False):
    require(isinstance(value, str) and len(value) <= 10000
            and (bool(value.strip()) or allow_empty))
    return value


def available_reviews(rows, plan):
    require(isinstance(rows, dict) and set(rows) == set(range(58))
            and isinstance(plan, dict) and isinstance(plan.get('units'), list)
            and len(plan['units']) == 58)
    counts = {'passed': 0, 'blocked_requires_review': 0}
    available = []
    for index in range(58):
        row, unit = rows[index], plan['units'][index]
        receipt = row['receipt']
        status = receipt.get('status')
        require(status in counts)
        counts[status] += 1
        if status == 'passed':
            continue
        require(receipt.get('blocked') is True and receipt.get('production_eligible') is False
                and receipt.get('review_only') is True and receipt.get('review_file') == 'review.wav'
                and receipt.get('review_sample_rate') == 24000
                and receipt.get('review_natural_eos') is True
                and type(receipt.get('review_sample_count')) is int
                and receipt['review_sample_count'] > 0)
        path = Path(row['path'])
        require(path.name == 'review.wav' and path.is_file()
                and not any(p.is_symlink() for p in (path, *path.parents))
                and 0 < path.stat().st_size <= MAX_WAV_BYTES)
        audio = path.read_bytes()
        require(digest(audio) == receipt.get('review_sha256'))
        info = wav_info(audio)
        require(info['sample_count'] == receipt['review_sample_count'])
        candidates = receipt.get('candidates')
        require(isinstance(candidates, list) and 0 < len(candidates) <= 2)
        matches = [c for c in candidates if c.get('sha256') == receipt['review_sha256']]
        require(len(matches) == 1)
        saved = matches[0]
        require(saved.get('natural_eos') is True and saved.get('signal', {}).get('blocked') is False
                and saved['signal'].get('samples') == info['sample_count']
                and type(saved.get('seed')) is int and isinstance(saved.get('asr'), dict))
        spoken = bounded_text(unit.get('spoken'))
        require(unit.get('mode') in ('reading', 'explain')
                and receipt.get('spoken_sha256') == digest(spoken.encode('utf-8'))
                and receipt.get('source_sha256') == unit.get('source_sha256')
                and receipt.get('unit_id') == unit.get('id'))
        asr = saved['asr']
        recognized = bounded_text(asr.get('recognized'), allow_empty=True)
        missing = asr.get('missing_critical', [])
        critical = unit.get('critical_terms', [])
        require(isinstance(missing, list) and isinstance(critical, list)
                and len(missing) <= 100 and len(critical) <= 100
                and all(isinstance(x, str) and len(x) <= 1000 for x in missing+critical)
                and type(asr.get('character_errors')) is int and asr['character_errors'] >= 0)
        available.append(dict(index=index, path=path, wav_sha256=digest(audio), info=info,
            unit_id=unit['id'], mode=unit['mode'], expected_spoken=spoken,
            saved_recognized=recognized, saved_seed=saved['seed'], critical_terms=critical,
            missing_critical=missing, character_errors=asr['character_errors']))
    require(counts == {'passed': 27, 'blocked_requires_review': 31}
            and len(available) == 31)
    return available


def select_reviews(available):
    require(isinstance(available, list) and len(available) == 31
            and len({x['index'] for x in available}) == 31)
    ordered = sorted(available, key=lambda x: (x['info']['sample_count'], x['index']))
    chosen = [(ordered[0], 'shortest_blocked_review')]
    critical = [x for x in ordered if x['missing_critical'] and x['index'] != chosen[0][0]['index']]
    if critical:
        chosen.append((critical[0], 'critical_term_disagreement'))
    for row in ordered:
        if len(chosen) == MAX_CLIPS:
            break
        if row['index'] not in {x['index'] for x, _ in chosen}:
            chosen.append((row, 'additional_short_blocked_review'))
    require(len(chosen) == MAX_CLIPS and len({x['index'] for x, _ in chosen}) == MAX_CLIPS)
    return chosen


def make_payload(chosen, request):
    require(isinstance(chosen, list) and 0 < len(chosen) <= MAX_CLIPS
            and len({x['index'] for x, _ in chosen}) == len(chosen))
    stream, clips = io.BytesIO(), []
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_STORED) as archive:
        for number, (row, reason) in enumerate(chosen, 1):
            require(reason in ('shortest_blocked_review', 'critical_term_disagreement',
                               'additional_short_blocked_review'))
            audio = row['path'].read_bytes()
            require(digest(audio) == row['wav_sha256'] and wav_info(audio) == row['info'])
            filename = f'clip-{number:02d}.wav'
            archive.writestr(filename, audio)
            clips.append(dict(number=number, unit_index=row['index'], unit_id=row['unit_id'],
                mode=row['mode'], selection_reason=reason, expected_spoken=row['expected_spoken'],
                saved_recognized=row['saved_recognized'], saved_candidate_seed=row['saved_seed'],
                critical_terms=row['critical_terms'], missing_critical=row['missing_critical'],
                saved_character_errors=row['character_errors'], wav_file=filename,
                wav_sha256=row['wav_sha256'], **row['info'],
                status='blocked_requires_review', original_saved_raw_candidate=True,
                production_eligible=False, approved=False))
        manifest = dict(schema=PAYLOAD_SCHEMA, request_id=request['request_id'],
            source_pin=full.PIN, clips=clips, synthesis_started=False,
            production_status_changed=False, original_checkpoint_written=False,
            human_listening_verified=False, production_eligible=False)
        encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')
        require(len(encoded) <= MAX_JSON_BYTES)
        archive.writestr('preview.json', encoded)
    plain = stream.getvalue()
    require(0 < len(plain) <= MAX_PLAIN_BYTES)
    return plain


def aad_for(request):
    return canonical(dict(schema=SCHEMA, request_id=request['request_id'],
                          recipient_spki_sha256=request['recipient_spki_sha256']))


def encrypt_payload(plain, request, *, clip_count):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    public = recipient(request)
    require(isinstance(plain, bytes) and 0 < len(plain) <= MAX_PLAIN_BYTES
            and type(clip_count) is int and 0 < clip_count <= MAX_CLIPS)
    key, nonce = os.urandom(32), os.urandom(12)
    aad = aad_for(request)
    cipher = AESGCM(key).encrypt(nonce, plain, aad)
    wrapped = public.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                             algorithm=hashes.SHA256(), label=aad))
    del key
    return dict(schema=SCHEMA, request_id=request['request_id'],
        recipient_spki_sha256=request['recipient_spki_sha256'],
        algorithm='RSA-OAEP-SHA256+AES-256-GCM',
        aad_b64=base64.b64encode(aad).decode('ascii'),
        wrapped_key_b64=base64.b64encode(wrapped).decode('ascii'),
        nonce_b64=base64.b64encode(nonce).decode('ascii'),
        ciphertext_b64=base64.b64encode(cipher).decode('ascii'),
        encrypted_payload_bytes=len(cipher), encrypted_payload_sha256=digest(cipher),
        clip_count=clip_count, private_plaintext_in_public_artifact=False,
        synthesis_started=False, production_eligible=False,
        original_checkpoint_written=False)


def export(directory, output, request):
    check_context(os.environ, os.getuid(), os.geteuid())
    from namespace_guard import validate_snapshot
    validate_snapshot(os.getuid(), os.getuid(), socket.if_nameindex(),
        Path('/proc/net/route').read_text(), os.readlink('/proc/self/ns/net'),
        os.environ.get('BORA_HOST_NETNS', ''), Path('/proc/net/ipv6_route').read_text())
    values = [os.environ.pop(name, '') for name in PRIVATE_KEYS]
    source_key, media_key = [base64.b64decode(value, validate=True) for value in values]
    del values
    require(len(source_key) == len(media_key) == 32 and source_key != media_key)
    def deny(*args, **kwargs):
        raise RuntimeError('outbound_forbidden')
    socket.socket.connect = deny
    socket.socket.connect_ex = deny
    socket.create_connection = deny
    import runtime as runtime
    campaign, recipe, _, _ = runtime.config()
    require(runtime.identity(recipe) == full.PIN['recipe_sha256'])
    full.inventory(directory)
    with runtime.quiet():
        plan, _ = runtime.source_plan('TB-001', campaign, source_key)
    del source_key
    require(runtime.identity(plan) == full.PIN['plan_sha256']
            and len(plan['units']) == 58
            and not any(u.get('review') or u.get('review_flags') for u in plan['units']))
    with tempfile.TemporaryDirectory(prefix='bora-private-owner-preview-',
                                     dir=os.environ['RUNNER_TEMP']) as temporary:
        with runtime.quiet():
            # These are temporary authenticated working copies, never writes to
            # existing checkpoints. Only blocked review files enter the payload.
            rows = runtime.restore(directory, media_key, plan, full.PIN['recipe_sha256'],
                full.PIN['render_settings_sha256'], Path(temporary))
            chosen = select_reviews(available_reviews(rows, plan))
            plain = make_payload(chosen, request)
            envelope = encrypt_payload(plain, request, clip_count=len(chosen))
            del plain
    del media_key
    full.d.dump(output, envelope)
    print(json.dumps(dict(status='owner_preview_encrypted', clips=envelope['clip_count'],
                          synthesized=0, promoted=0, checkpoint_writes=0)), flush=True)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('download', 'export'))
    parser.add_argument('--directory', required=True)
    parser.add_argument('--output')
    args = parser.parse_args()
    request = check_request()
    if args.mode == 'download':
        require(args.output is None)
        full.download(args.directory)
    else:
        require(bool(args.output))
        export(args.directory, args.output, request)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('{"status":"failed","reason":"owner_listening_preview_validation_failed"}', flush=True)
        raise SystemExit(1)
