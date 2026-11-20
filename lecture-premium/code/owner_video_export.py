"""Package exact published lesson audio for a reviewed, owner-only video edit."""

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import zipfile

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import delivery
import lecture_crypto as crypto
import lecture_pack as pack
from namespace_guard import validate_snapshot


REPOSITORY = 'quddnr0107-lgtm/korean-voice'
RELEASES = {
    'DR': ('bora-fp32-dr-single-pass-v1-20261114',
           '96010d3c26f0b27180162c2cda9488f1a81622defdff57593e3167202181984d', 78),
    'TB': ('bora-fp32-tb-single-pass-v1-20261114',
           '174fce697dc12f9aa3d6da7d6e98ddd8e9c1a3c79ac7e6ba39ef1b9de2dd1290', 25),
    'RL': ('bora-fp32-rl-single-pass-v1-20261114',
           '4be4c78bfbb45affd31ecdad418b750397b6fe37856fa563caa4b8f1a99131ef', 34),
    'RL-027': ('bora-fp32-rl027-art15p3-v2-20261119',
               '2370ff56cce9c4ffa23d53fae1ec9c9580d6d4c28dc035187816bba524331fc7', 1),
}
FIRST_TWO = {'TB-001', 'TB-002'}
CONTEXT = b'muhan/owner-video-export/v1/'
REQUEST = Path('bora-production/owner-video-export-request.json')


def course_ids(course, count):
    if course == 'RL-027':
        return {'RL-027'}
    return {f'{course}-{number:03d}' for number in range(1, count + 1)}


def groups():
    shards = {}
    for course, (_, _, count) in RELEASES.items():
        ids = sorted(course_ids(course, count) - FIRST_TWO - ({'RL-027'} if course == 'RL' else set()))
        for start in range(0, len(ids), 8):
            shards[f'{course.lower()}-{start // 8 + 1:02d}'] = (course, ids[start:start + 8])
    unique = {identity for _, ids in shards.values() for identity in ids}
    if len(unique) != 135 or sum(len(ids) for _, ids in shards.values()) != 135:
        raise ValueError('export scope mismatch')
    return shards


def recipient():
    config = delivery.loads(delivery.read(REQUEST, 4096))
    if set(config) != {'schema', 'recipient_public_key'} or config['schema'] != 'owner-video-export/v1':
        raise ValueError('request schema')
    public_key = serialization.load_der_public_key(base64.b64decode(config['recipient_public_key'], validate=True))
    if not isinstance(public_key, rsa.RSAPublicKey) or public_key.key_size != 3072:
        raise ValueError('reviewed recipient key type')
    return public_key


def read_index(directory, course):
    tag, expected_digest, count = RELEASES[course]
    raw = delivery.read(directory / 'index.json', 128 * 1024)
    if hashlib.sha256(raw).hexdigest() != expected_digest:
        raise ValueError('release index digest')
    index = delivery.loads(raw)
    if (set(index) != {'version', 'release_tag', 'count', 'lessons'} or
            index['version'] != 'integrated-lecture-index/v2' or index['release_tag'] != tag or
            index['count'] != count or len(index['lessons']) != count):
        raise ValueError('release index scope')
    result = {}
    for row in index['lessons']:
        ident = row['id']
        prefix = f'https://github.com/{REPOSITORY}/releases/download/{tag}/'
        if (set(row) != {'id', 'descriptor_url', 'sha256'} or ident in result or
                row['descriptor_url'] != prefix + delivery.descriptor_name(ident) or
                not re.fullmatch('[a-f0-9]{64}', row['sha256'])):
            raise ValueError('release index descriptor row')
        result[ident] = row['sha256']
    if set(result) != course_ids(course, count):
        raise ValueError('release index coverage')
    return result


def download(directory, course, lesson_ids):
    directory.mkdir(mode=0o700)
    tag = RELEASES[course][0]
    command = ['gh', 'release', 'download', tag, '--repo', REPOSITORY, '--dir', str(directory)]
    subprocess.run([*command, '--pattern', 'index.json'], check=True)
    entries = read_index(directory, course)
    patterns = []
    for ident in lesson_ids:
        if ident not in entries:
            raise ValueError('requested lesson outside release')
        patterns.extend(['--pattern', delivery.descriptor_name(ident)])
    subprocess.run(command + patterns, check=True)
    patterns.clear()
    for ident in lesson_ids:
        descriptor = descriptor_for(directory, ident, entries[ident])
        for kind in ('audio', 'content'):
            patterns.extend(['--pattern', delivery.media_name(ident, kind, descriptor[kind])])
    subprocess.run(command + patterns, check=True)


def descriptor_for(directory, ident, digest):
    raw = delivery.read(directory / delivery.descriptor_name(ident), 512 * 1024)
    if crypto.digest(raw) != digest:
        raise ValueError('release descriptor digest')
    return delivery.check_descriptor(delivery.loads(raw), ident)


def recover(directory, ident, digest, media_key):
    descriptor = descriptor_for(directory, ident, digest)
    audio = descriptor['audio']
    content = descriptor['content']
    audio_name = delivery.media_name(ident, 'audio', audio)
    content_name = delivery.media_name(ident, 'content', content)
    blob = delivery.read(directory / audio_name, delivery.cap_for(audio_name))
    metadata = delivery.read(directory / content_name, delivery.cap_for(content_name))
    delivery.check_media(audio, blob, ident, 'audio')
    delivery.check_media(content, metadata, ident, 'private-metadata')
    raw, _ = crypto.open_range(media_key, content, metadata, expected_id=ident,
                               expected_kind='private-metadata', expected_key_id=delivery.KEY_ID)
    record = delivery.loads(raw)
    if (record['id'] != ident or record['audio_pack_sha256'] != audio['pack_sha256'] or
            record['total_samples'] != audio['total_samples'] or record['sample_rate'] != audio['sample_rate'] or
            not isinstance(record['narration_text'], str) or not record['segments']):
        raise ValueError('audio and original timing identity')
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, 'w', zipfile.ZIP_STORED) as archive:
        archive.writestr('metadata.json', raw)
        archive.writestr('descriptor.json', delivery.encoded(descriptor))
        for part in audio['parts']:
            index = part['part_index']
            start = part['pack_offset']
            end = start + part['cipher_bytes']
            mp3 = pack.open_part(media_key, part, blob[start:end], expected_id=ident,
                                 expected_part_index=index, expected_pack_id=audio['object_id'])
            archive.writestr(f'part-{index:03d}.mp3', mp3)
    return bundle.getvalue()


def seal(public_key, ident, payload):
    context = CONTEXT + ident.encode('ascii')
    aes_key = AESGCM.generate_key(bit_length=256)
    wrapped = public_key.encrypt(aes_key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                                                       algorithm=hashes.SHA256(), label=context))
    nonce = os.urandom(12)
    return wrapped + nonce + AESGCM(aes_key).encrypt(nonce, payload, context)


def export(directory, output, course, lesson_ids, expected_uid, host_namespace):
    validate_snapshot(os.getuid(), expected_uid, socket.if_nameindex(),
                      Path('/proc/net/route').read_text(), os.readlink('/proc/self/ns/net'),
                      host_namespace, Path('/proc/net/ipv6_route').read_text())
    media_key = base64.b64decode(os.environ.pop('ADMIN_LAW_PREMIUM_MEDIA_KEY_B64'), validate=True)
    if len(media_key) != 32:
        raise ValueError('media key not configured')
    public_key = recipient()
    entries = read_index(directory, course)
    output.mkdir(mode=0o700)
    for ident in lesson_ids:
        encrypted = seal(public_key, ident, recover(directory, ident, entries[ident], media_key))
        with (output / f'{ident}.owner-media.bin').open('xb') as handle:
            handle.write(encrypted)
        print(f'{ident}: verified and encrypted for owner video edit', flush=True)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('matrix')
    fetch_args = sub.add_parser('download')
    fetch_args.add_argument('shard', choices=groups())
    fetch_args.add_argument('directory', type=Path)
    export_args = sub.add_parser('export')
    export_args.add_argument('shard', choices=groups())
    export_args.add_argument('directory', type=Path)
    export_args.add_argument('output', type=Path)
    export_args.add_argument('expected_uid', type=int)
    export_args.add_argument('host_namespace')
    args = parser.parse_args()
    if args.action == 'matrix':
        recipient()
        print(json.dumps({'shard': list(groups())}, separators=(',', ':')))
        return
    course, lesson_ids = groups()[args.shard]
    if args.action == 'download':
        download(args.directory, course, lesson_ids)
    else:
        export(args.directory, args.output, course, lesson_ids, args.expected_uid, args.host_namespace)


if __name__ == '__main__':
    main()
