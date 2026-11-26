"""Owner-only video envelopes for one reviewed set of archived MS lessons."""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import socket
import zipfile

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import ms034_owner_export
import lecture_crypto as crypto
import lecture_pack as pack
from namespace_guard import validate_snapshot
import transport

RELEASE = 445935269
TAG = 'bora-fp32-ms-single-pass-v1-20261114'
PIN = '7af5e31d59f042b31b86e5e1d98d72534a9fff18f01e2e2bf8c62c59416bddb3'
CONTEXT = b'muhan/owner-video-export/v1/'
EXCLUDED = {5,6,18,21,22,28,31,34,53,54,71,73,75,94,97,103,115,141,144}


def plan():
    keys = [f'MS-{i:03d}' for i in range(1,167) if i not in EXCLUDED]
    if len(keys) != 147:
        raise ValueError('exact lesson set')
    return {f'ms-{i//8+1:02d}':keys[i:i+8] for i in range(0,len(keys),8)}


def canonical(lessons):
    return json.dumps(lessons, sort_keys=True, separators=(',',':')).encode()


def validate_manifest(raw):
    if hashlib.sha256(raw).hexdigest() != PIN:
        raise ValueError('manifest pin')
    lessons = transport._json(raw)
    if set(lessons) != {ident for ids in plan().values() for ident in ids}:
        raise ValueError('exact lesson set')
    ids = set()
    for ident, triple in lessons.items():
        kinds = {name.split('.')[1] for name in triple}
        if len(triple) != 3 or kinds != {'descriptor', 'audio', 'content'}:
            raise ValueError('exact media triple')
        for name, (asset, size, digest) in triple.items():
            if (not re.fullmatch(re.escape(ident) + r'\.(?:descriptor\.json|(?:audio|content)\.[a-f0-9]{32}\.bin)', name) or
                type(asset) is not int or asset <= 0 or asset in ids or
                type(size) is not int or not 0 < size <= transport.FINAL_OBJECT_CAPS.get(name.split('.')[1], transport.DESCRIPTOR_CAP) or
                not re.fullmatch('[a-f0-9]{64}', digest)):
                raise ValueError('invalid asset pin')
            ids.add(asset)
    return lessons


def recipient():
    return ms034_owner_export.recipient()


def seal(public, ident, payload):
    label = CONTEXT + ident.encode('ascii')
    key = AESGCM.generate_key(bit_length=256)
    wrapped = public.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                                                algorithm=hashes.SHA256(), label=label))
    nonce = os.urandom(12)
    return wrapped + nonce + AESGCM(key).encrypt(nonce, payload, label)


def download(directory, ids, backend=None):
    backend = backend or transport.GitHubBackend()
    source = backend.release(TAG)
    if not source or source['id'] != RELEASE or source['tag_name'] != TAG or not source['draft']:
        raise ValueError('source release drift')
    catalog = transport._final_assets(backend, source, 'MS')
    expected = {ident for group in plan().values() for ident in group}
    pinned = {ident: {} for ident in expected}
    for name, a in catalog.items():
        ident = name.partition('.')[0]
        if ident in expected:
            pinned[ident][name] = [a['id'],a['size'],a['digest'].removeprefix('sha256:')]
    raw = canonical(pinned)
    lessons = validate_manifest(raw)
    directory.mkdir(mode=0o700)
    with (directory/'manifest.json').open('xb') as f:
        f.write(raw)
    for ident in ids:
        dest = directory / ident
        dest.mkdir(mode=0o700)
        for name, (asset_id, size, sha) in lessons[ident].items():
            asset = catalog.get(name)
            if not asset or (asset['id'],asset['size'],asset.get('digest')) != (asset_id,size,'sha256:'+sha):
                raise ValueError('pinned asset changed')
            transport._fetch_final_object(backend, asset, dest / name, size)


def verify_input(directory, triple):
    if directory.is_symlink() or {p.name for p in directory.iterdir()} != set(triple):
        raise ValueError('exact input files')
    for name, (_,size,sha) in triple.items():
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
            raise ValueError('pinned input size')
        with path.open('rb') as h:
            if hashlib.file_digest(h,'sha256').hexdigest() != sha:
                raise ValueError('pinned input hash')
    transport.validate_final_directory(directory,'MS',partial=True,profile='single_pass')


def recover(directory, ident, media_key, triple):
    if type(media_key) is not bytes or len(media_key) != 32:
        raise ValueError('media key shape')
    directory = Path(directory)
    verify_input(directory, triple)
    descriptor, objects = transport._read_descriptor(directory / (ident + '.descriptor.json'), ident)
    audio, content = descriptor['audio'], descriptor['content']
    audio_blob = (directory / objects['audio']).read_bytes()
    content_blob = (directory / objects['content']).read_bytes()
    raw, _ = crypto.open_range(media_key, content, content_blob, expected_id=ident,
                               expected_kind='private-metadata', expected_key_id=transport.KEY_ID)
    record = transport._json(raw)
    if (record['id'] != ident or record['audio_pack_sha256'] != audio['pack_sha256']
            or record['total_samples'] != audio['total_samples']
            or record['sample_rate'] != audio['sample_rate']
            or not isinstance(record['narration_text'], str) or not record['segments']):
        raise ValueError('audio and timing identity')
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, 'w', zipfile.ZIP_STORED) as archive:
        archive.writestr('metadata.json', raw)
        archive.writestr('descriptor.json', (directory / (ident + '.descriptor.json')).read_bytes())
        for part in audio['parts']:
            i = part['part_index']
            start = part['pack_offset']
            mp3 = pack.open_part(media_key, part, audio_blob[start:start + part['cipher_bytes']],
                                 expected_id=ident, expected_part_index=i,
                                 expected_pack_id=audio['object_id'])
            archive.writestr(f'part-{i:03d}.mp3', mp3)
    return bundle.getvalue()



def export(directory, output, ids, uid, host_ns):
    validate_snapshot(os.getuid(), uid, socket.if_nameindex(),
                      Path('/proc/net/route').read_text(), os.readlink('/proc/self/ns/net'),
                      host_ns, Path('/proc/net/ipv6_route').read_text())
    public = recipient()
    key = base64.b64decode(os.environ.pop('ADMIN_LAW_PREMIUM_MEDIA_KEY_B64'),validate=True)
    if len(key) != 32:
        raise ValueError('media key shape')
    if directory.is_symlink() or {p.name for p in directory.iterdir()} != {*ids,'manifest.json'}:
        raise ValueError('exact shard inputs')
    manifest = directory/'manifest.json'
    if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size>96*1024:
        raise ValueError('manifest file')
    lessons = validate_manifest(manifest.read_bytes())
    os.umask(0o077)
    output.mkdir(mode=0o700)
    for ident in ids:
        triple = lessons[ident]
        encrypted = seal(public, ident, recover(directory / ident, ident, key, triple))
        with (output / (ident+'.owner-media.bin')).open('xb') as f:
            f.write(encrypted)
        print(f'{ident}: original MP3 and timing authenticated and encrypted for existing recipient',flush=True)


def main():
    shards = plan()
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='mode',required=True)
    sub.add_parser('matrix')
    for mode in ('download','export'):
        sp = sub.add_parser(mode)
        sp.add_argument('shard',choices=shards)
        sp.add_argument('input',type=Path)
        if mode == 'export':
            sp.add_argument('output',type=Path)
            sp.add_argument('uid',type=int)
            sp.add_argument('host_namespace')
    opt=p.parse_args()
    if opt.mode == 'matrix':
        recipient()
        print(json.dumps({'shard':list(shards)},separators=(',',':')))
    elif opt.mode == 'download':
        download(opt.input,shards[opt.shard])
    else:
        export(opt.input,opt.output,shards[opt.shard],opt.uid,opt.host_namespace)

if __name__ == '__main__':
    main()
