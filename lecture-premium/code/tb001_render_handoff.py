"""One-lesson render handoff, retaining the protected owner export implementation."""
import argparse
import hashlib
import os
from pathlib import Path

import owner_video_export as owner

LESSON = 'TB-001'
COURSE = 'TB'
REQUEST = Path('bora-production/tb001-render-handoff-request.json')
REQUEST_SHA256 = 'b6925b8e3cf35f96dccac8619436b8468b24ef35faa260edc08e764fbaadebde'
INDEX_SHA256 = '174fce697dc12f9aa3d6da7d6e98ddd8e9c1a3c79ac7e6ba39ef1b9de2dd1290'


def validate_scope():
    if hashlib.sha256(REQUEST.read_bytes()).hexdigest() != REQUEST_SHA256:
        raise ValueError('Reviewed recipient request changed')
    if owner.RELEASES[COURSE] != ('bora-fp32-tb-single-pass-v1-20261114', INDEX_SHA256, 25):
        raise ValueError('Published Bora release pin changed')
    # Only this isolated process uses the one-lesson recipient; no existing request is changed.
    owner.REQUEST = REQUEST
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
