"""Single-lesson owner export. No production release or plaintext upload."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import sys
import zipfile

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import lecture_crypto as crypto
import lecture_pack as pack
import transport
from namespace_guard import validate_snapshot

LESSON = 'MS-034'
ASSETS = {
    'MS-034.audio.9c57a6aa16f4f0c805297593269ad733.bin': (34656583, 'e3d8b372195a912c15488e6c4fe5687cb4bddddcacbbb6b48b65c3c6cc1ef40c'),
    'MS-034.content.431134ab8853982c264ac9bd8f3ced1a.bin': (652932, '6eb05d027fcdc7034a78dbd440bd2404a4f03883c7559c47da85887adefabd89'),
    'MS-034.descriptor.json': (62214, 'b79d8aff2f44e16c59094044e7c725e17c14c6497a017f561f7bcc957c2e4dfb'),
}
RECIPIENT_CONFIG = Path(__file__).with_name('owner-video-export-request.json')
RECIPIENT_CONFIG_BLOB = '49284d2d634c665dfddacd02b165c0f2dc279976'
CONTEXT = b'muhan/owner-video-export/v1/MS-034'


def verify_pins(directory, pins):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('input directory')
    if {p.name for p in directory.iterdir()} != set(pins):
        raise ValueError('exact input allowlist')
    for name, (size, digest) in pins.items():
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
            raise ValueError('immutable asset size')
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('immutable asset digest')
    transport.validate_final_directory(directory, 'MS', partial=True, profile='single_pass')


def recover(directory, media_key, *, pins=ASSETS):
    if type(media_key) is not bytes or len(media_key) != 32:
        raise ValueError('media key shape')
    directory = Path(directory)
    verify_pins(directory, pins)
    descriptor, objects = transport._read_descriptor(directory / (LESSON + '.descriptor.json'), LESSON)
    audio, content = descriptor['audio'], descriptor['content']
    audio_blob = (directory / objects['audio']).read_bytes()
    content_blob = (directory / objects['content']).read_bytes()
    raw, _ = crypto.open_range(media_key, content, content_blob, expected_id=LESSON,
                               expected_kind='private-metadata', expected_key_id=transport.KEY_ID)
    record = transport._json(raw)
    if (record['id'] != LESSON or record['audio_pack_sha256'] != audio['pack_sha256']
            or record['total_samples'] != audio['total_samples']
            or record['sample_rate'] != audio['sample_rate']
            or not isinstance(record['narration_text'], str) or not record['segments']):
        raise ValueError('audio and timing identity')
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, 'w', zipfile.ZIP_STORED) as archive:
        archive.writestr('metadata.json', raw)
        archive.writestr('descriptor.json', (directory / (LESSON + '.descriptor.json')).read_bytes())
        for part in audio['parts']:
            i = part['part_index']
            start = part['pack_offset']
            mp3 = pack.open_part(media_key, part, audio_blob[start:start + part['cipher_bytes']],
                                 expected_id=LESSON, expected_part_index=i,
                                 expected_pack_id=audio['object_id'])
            archive.writestr(f'part-{i:03d}.mp3', mp3)
    return bundle.getvalue()


def recipient():
    raw = RECIPIENT_CONFIG.read_bytes()
    git_blob = hashlib.sha1(b'blob ' + str(len(raw)).encode('ascii') + b'\0' + raw).hexdigest()
    if git_blob != RECIPIENT_CONFIG_BLOB:
        raise ValueError('owner recipient config changed')
    config = transport._json(raw)
    if set(config) != {'schema', 'recipient_public_key'} or config['schema'] != 'owner-video-export/v1':
        raise ValueError('owner recipient schema')
    public = serialization.load_der_public_key(base64.b64decode(config['recipient_public_key'], validate=True))
    if not isinstance(public, rsa.RSAPublicKey) or public.key_size != 3072:
        raise ValueError('owner recipient type')
    return public


def seal_for_owner(public, payload):
    if not isinstance(public, rsa.RSAPublicKey) or public.key_size != 3072:
        raise ValueError('owner recipient type')
    envelope_key = AESGCM.generate_key(bit_length=256)
    wrapped = public.encrypt(envelope_key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                                                        algorithm=hashes.SHA256(), label=CONTEXT))
    nonce = os.urandom(12)
    return wrapped + nonce + AESGCM(envelope_key).encrypt(nonce, payload, CONTEXT)


def main():
    directory, target, uid, host_namespace = sys.argv[1:]
    validate_snapshot(os.getuid(), int(uid), socket.if_nameindex(),
                      Path('/proc/net/route').read_text(), os.readlink('/proc/self/ns/net'),
                      host_namespace, Path('/proc/net/ipv6_route').read_text())
    public = recipient()
    media_key = base64.b64decode(os.environ.pop('ADMIN_LAW_PREMIUM_MEDIA_KEY_B64'), validate=True)
    ciphertext = seal_for_owner(public, recover(directory, media_key))
    os.umask(0o077)
    with Path(target).open('xb') as handle:
        handle.write(ciphertext)
    print('MS-034: complete original MP3 parts authenticated and sealed for the existing owner recipient.')


if __name__ == '__main__':
    main()
