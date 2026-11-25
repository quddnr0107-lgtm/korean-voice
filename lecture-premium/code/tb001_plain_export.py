"""One authorized TB-001 export: existing MP3 bytes and spoken-caption timing only."""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import zipfile

import owner_video_export as owner

LESSON = 'TB-001'
COURSE = 'TB'
RELEASE = ('bora-fp32-tb-single-pass-v1-20261114',
           '174fce697dc12f9aa3d6da7d6e98ddd8e9c1a3c79ac7e6ba39ef1b9de2dd1290', 25)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_scope():
    require(owner.RELEASES[COURSE] == RELEASE, 'Release pin changed')


def plain_package(payload):
    """Remove private production fields; retain the exact audio and its clock."""
    with zipfile.ZipFile(io.BytesIO(payload), 'r') as src:
        names = src.namelist()
        require(len(names) == len(set(names)), 'Duplicate source member')
        record = json.loads(src.read('metadata.json'))
        descriptor = json.loads(src.read('descriptor.json'))
        audio = descriptor['audio']
        rate, total = audio['sample_rate'], audio['total_samples']
        require(record['id'] == LESSON and audio['lesson_id'] == LESSON,
                'Wrong lesson')
        require(type(rate) is int and rate == 44100 and type(total) is int and total > 0,
                'Invalid sample clock')
        require(record['sample_rate'] == rate and record['total_samples'] == total,
                'Timeline identity mismatch')
        require(record['audio_pack_sha256'] == audio['pack_sha256'], 'Audio identity mismatch')
        parts = audio['parts']
        require(isinstance(parts, list) and bool(parts), 'Missing audio parts')
        expected = {'metadata.json', 'descriptor.json'} | {f'part-{i:03d}.mp3' for i in range(len(parts))}
        require(set(names) == expected, 'Unexpected source members')
        result = io.BytesIO()
        manifest = dict(schema='tb001-plain-render-input/v1', id=LESSON,
                        sample_rate=rate, total_samples=total, parts=[], segments=[])
        cursor = 0
        with zipfile.ZipFile(result, 'w', zipfile.ZIP_STORED) as dst:
            for i, part in enumerate(parts):
                start, end = part['start_sample'], part['end_sample']
                require(part['part_index'] == i and type(start) is int and type(end) is int
                        and start == cursor and start < end <= total, 'Part clock mismatch')
                name = f'part-{i:03d}.mp3'
                data = src.read(name)
                require(len(data) == part['plain_bytes'] and bool(data), 'Audio length mismatch')
                dst.writestr(name, data)
                manifest['parts'].append(dict(file=name, start_sample=start, end_sample=end,
                                              sha256=hashlib.sha256(data).hexdigest()))
                cursor = end
            require(cursor == total, 'Incomplete audio coverage')
            previous = 0
            for segment in record['segments']:
                start, end, text = segment['start_sample'], segment['end_sample'], segment['text']
                require(type(start) is int and type(end) is int and previous <= start < end <= total
                        and isinstance(text, str) and bool(text.strip()), 'Invalid spoken caption')
                manifest['segments'].append(dict(start_sample=start, end_sample=end, text=text))
                previous = end
            require(bool(manifest['segments']) and ''.join(s['text'] for s in manifest['segments'])
                    == record['narration_text'], 'Incomplete spoken text')
            dst.writestr('timeline.json', json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        return result.getvalue()


def export(directory, output, expected_uid, host_namespace):
    owner.validate_snapshot(os.getuid(), expected_uid, socket.if_nameindex(),
                            Path('/proc/net/route').read_text(), os.readlink('/proc/self/ns/net'),
                            host_namespace, Path('/proc/net/ipv6_route').read_text())
    media_key = base64.b64decode(os.environ.pop('ADMIN_LAW_PREMIUM_MEDIA_KEY_B64'), validate=True)
    require(len(media_key) == 32, 'Media key not configured')
    entries = owner.read_index(directory, COURSE)
    data = plain_package(owner.recover(directory, LESSON, entries[LESSON], media_key))
    with output.open('xb') as handle:
        handle.write(data)
    print('TB-001: authenticated MP3 parts and spoken timeline exported without recipient encryption.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    fetch = sub.add_parser('download')
    fetch.add_argument('directory', type=Path)
    job = sub.add_parser('export')
    job.add_argument('directory', type=Path)
    job.add_argument('output', type=Path)
    job.add_argument('expected_uid', type=int)
    job.add_argument('host_namespace')
    args = parser.parse_args(argv)
    os.umask(0o077)
    validate_scope()
    if args.action == 'download':
        owner.download(args.directory, COURSE, [LESSON])
    else:
        export(args.directory, args.output, args.expected_uid, args.host_namespace)


if __name__ == '__main__':
    main()
