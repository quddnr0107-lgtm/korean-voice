"""Scoped owner-only TB-001 export; delegates to the existing protected exporter."""
import argparse
import hashlib
import os
from pathlib import Path

import owner_video_export as owner

LESSON = 'TB-001'
COURSE = 'TB'
RECIPIENT_BLOB = '49284d2d634c665dfddacd02b165c0f2dc279976'
INDEX_SHA256 = '174fce697dc12f9aa3d6da7d6e98ddd8e9c1a3c79ac7e6ba39ef1b9de2dd1290'


def validate_scope():
    raw = owner.REQUEST.read_bytes()
    git_blob = hashlib.sha1(b'blob ' + str(len(raw)).encode('ascii') + b'\0' + raw).hexdigest()
    if git_blob != RECIPIENT_BLOB:
        raise ValueError('Existing owner recipient request changed; review required')
    if owner.RELEASES[COURSE] != ('bora-fp32-tb-single-pass-v1-20261114', INDEX_SHA256, 25):
        raise ValueError('Published Bora release pin changed; review required')
    owner.recipient()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    fetch = sub.add_parser('download')
    fetch.add_argument('directory', type=Path)
    export = sub.add_parser('export')
    export.add_argument('directory', type=Path)
    export.add_argument('output', type=Path)
    export.add_argument('expected_uid', type=int)
    export.add_argument('host_namespace')
    args = parser.parse_args(argv)
    os.umask(0o077)
    validate_scope()
    if args.action == 'download':
        owner.download(args.directory, COURSE, [LESSON])
    else:
        owner.export(args.directory, args.output, COURSE, [LESSON], args.expected_uid, args.host_namespace)


if __name__ == '__main__':
    main()
