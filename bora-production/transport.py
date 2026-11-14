"""Keyless, immutable release transport for FP32 production profiles.

Only schema-checked ciphertext leaves the runner. No source/media keys, deletion,
clobber, arbitrary repositories, URLs, tags or command execution are supported.
The gh CLI owns GitHub's authenticated HTTPS and asset redirects.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from urllib.parse import quote, urlsplit
import zipfile

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from lecture_crypto import CHUNK, namespace_for, validate as validate_media
from lecture_pack import validate_pack

REPOSITORY = 'quddnr0107-lgtm/korean-voice'
COURSES = {'TB': 25, 'RL': 34, 'DR': 78, 'MS': 166}
KEY_ID = 'integrated-lectures-media-v1'
PROFILES = ('eager', 'compiled', 'single_pass')
COMPILED_LESSON = 'TB-002'
COMPILED_STAGING_TAG = 'bora-fp32-tb-compiled-pilot-staging-20261114'
MAX_BUNDLE = 1024 * 1024 * 1024
MAX_MEMBER = 256 * 1024 * 1024
FINAL_OBJECT_CAPS = {'audio': 128 * 1024 * 1024, 'content': 9 * 1024 * 1024}
DESCRIPTOR_CAP = 512 * 1024
REPORT_FIELDS = {'schema', 'lesson_id', 'shard', 'start', 'end', 'passed', 'blocked', 'pending',
                 'timed_out', 'recipe_sha256', 'plan_sha256', 'render_settings_sha256', 'run_id', 'run_attempt'}
ENVELOPE_FIELDS = {'version', 'lesson_id', 'unit_index', 'plan_sha256', 'recipe_sha256',
                   'render_settings_sha256', 'manifest', 'cipher_b64'}
BUNDLE_RE = re.compile(r'(?P<lesson>(?:TB|RL|DR|MS)-[0-9]{3})-s(?P<shard>[0-9]{3})-r(?P<run>[1-9][0-9]{0,19})-a(?P<attempt>[1-9][0-9]{0,9})\.zip')
HASH_RE = re.compile(r'[a-f0-9]{64}')
FINAL_ASSET_RE = re.compile(r'(?P<lesson>(?:TB|RL|DR|MS)-[0-9]{3})\.(?:descriptor\.json|(?:audio|content)\.[a-f0-9]{32}\.bin)')


class TransportError(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise TransportError(code)


def assert_keyless():
    require(not any(re.search(r'(?:SOURCE|MEDIA).*KEY|KEY.*(?:SOURCE|MEDIA)', name, re.I)
                    for name in os.environ), 'source_or_media_key_environment_present')
    configured = os.environ.get('GITHUB_REPOSITORY')
    require(configured is None or configured == REPOSITORY, 'repository_environment_mismatch')


def course_ids(course):
    require(course in COURSES, 'course_scope')
    return [f'{course}-{i:03d}' for i in range(1, COURSES[course] + 1)]


def _profile(course, profile, final=False):
    course_ids(course)
    require(profile in PROFILES, 'execution_profile_scope')
    if profile == 'compiled':
        require(course == 'TB', 'compiled_course_scope')
        require(not final, 'compiled_final_forbidden')


def tag_for(course, final=False, *, profile='eager'):
    _profile(course, profile, final)
    if profile == 'compiled':
        return COMPILED_STAGING_TAG
    if profile == 'single_pass':
        return f'bora-fp32-{course.lower()}-single-pass-{"v1" if final else "staging"}-20261114'
    return f'bora-fp32-{course.lower()}-{"v1" if final else "staging"}-20261112'


def _lesson(ident, course=None):
    try:
        namespace_for(ident)
    except (ValueError, TypeError):
        raise TransportError('lesson_scope') from None
    require(course is None or ident in course_ids(course), 'lesson_course_mismatch')
    return ident.split('-')[0]


def _profile_lesson(ident, course=None, profile='eager'):
    chosen = _lesson(ident, course)
    _profile(chosen, profile)
    require(profile != 'compiled' or ident == COMPILED_LESSON, 'compiled_lesson_scope')
    return chosen


def _sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def _hash(value):
    return isinstance(value, str) and HASH_RE.fullmatch(value) is not None


def _int(value, low=0, high=1000000):
    return type(value) is int and low <= value <= high


def _safe(path):
    path = Path(path).absolute()
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'symlink_path')
    return path


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(TransportError('nonfinite_json')))
    except (UnicodeError, json.JSONDecodeError):
        raise TransportError('invalid_json') from None


def _keys(value, fields):
    require(isinstance(value, dict) and set(value) == fields, 'unsafe_public_schema')


def _cipher(manifest, blob, lesson_id, kind):
    try:
        validate_media(manifest, blob)
    except (ValueError, TypeError, KeyError):
        raise TransportError('cipher_manifest_invalid') from None
    require(manifest['lesson_id'] == lesson_id and manifest['kind'] == kind
            and manifest['key_id'] == KEY_ID, 'cipher_scope')
    for index, wanted in enumerate(manifest['cipher_sha256']):
        require(hashlib.sha256(blob[index*(CHUNK+16):(index+1)*(CHUNK+16)]).hexdigest() == wanted,
                'cipher_hash_mismatch')


def validate_bundle(path, course, *, profile='eager'):
    path = _safe(path)
    match = BUNDLE_RE.fullmatch(path.name)
    require(match is not None and path.is_file() and 0 < path.stat().st_size <= MAX_BUNDLE, 'bundle_name_or_size')
    ident = match['lesson']
    _profile_lesson(ident, course, profile)
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            require(len(names) == len(set(names)) and names.count('report.json') == 1
                    and len(names) <= 10001, 'bundle_members')
            require(sum(info.file_size for info in infos) <= MAX_BUNDLE, 'bundle_expanded_size')
            for info in infos:
                require(info.filename == 'report.json' or re.fullmatch(r'unit-[0-9]{5}\.sealed\.json', info.filename),
                        'unapproved_bundle_member')
                require(not info.is_dir() and not stat.S_ISLNK(info.external_attr >> 16)
                        and not info.flag_bits & 1 and 0 < info.file_size <= MAX_MEMBER, 'bundle_member_type_or_size')
            require(archive.getinfo('report.json').file_size <= 16384, 'report_size')
            report = _json(archive.read('report.json'))
            _keys(report, REPORT_FIELDS)
            require(report['schema'] == 'bora-fp32-shard-report/v1' and report['lesson_id'] == ident,
                    'report_scope')
            for field in ('shard', 'start', 'end', 'passed', 'blocked', 'pending'):
                require(_int(report[field], high=99999), 'report_integer')
            require(report['shard'] == int(match['shard']) and report['start'] < report['end']
                    and report['passed'] + report['blocked'] + report['pending'] == report['end'] - report['start'],
                    'report_counts')
            require(type(report['timed_out']) in (bool, int) and report['timed_out'] in (0, 1), 'report_timeout')
            require(str(report['run_id']) == match['run'] and str(report['run_attempt']) == match['attempt'],
                    'report_run_identity')
            for field in ('plan_sha256', 'recipe_sha256', 'render_settings_sha256'):
                require(_hash(report[field]), 'report_pin')
            indices = set()
            for name in names:
                if name == 'report.json':
                    continue
                index = int(name[5:10])
                envelope = _json(archive.read(name))
                _keys(envelope, ENVELOPE_FIELDS)
                require(envelope['version'] == 'bora-fp32-unit-envelope/v1' and envelope['lesson_id'] == ident
                        and _int(envelope['unit_index'], high=99999) and envelope['unit_index'] == index
                        and report['start'] <= index < report['end'], 'envelope_scope')
                require(all(envelope[k] == report[k] for k in ('plan_sha256', 'recipe_sha256', 'render_settings_sha256')),
                        'envelope_pin')
                try:
                    cipher = base64.b64decode(envelope['cipher_b64'], validate=True)
                    require(base64.b64encode(cipher).decode('ascii') == envelope['cipher_b64'], 'cipher_base64')
                except (ValueError, TypeError):
                    raise TransportError('cipher_base64') from None
                _cipher(envelope['manifest'], cipher, ident, 'checkpoint')
                indices.add(index)
            # Counts include authenticated units restored from prior bundles;
            # only newly produced envelopes are uploaded on a resumed shard.
            # This public report is inventory, never the assembly QA proof.
            require(len(indices) <= report['passed'] + report['blocked'], 'envelope_count')
    except (zipfile.BadZipFile, RuntimeError, OSError):
        raise TransportError('invalid_cipher_bundle') from None
    return report


def _asset_url(url, *, host, path):
    require(isinstance(url, str), 'asset_url')
    parsed = urlsplit(url)
    require(parsed.scheme == 'https' and parsed.hostname == host and parsed.netloc == host
            and parsed.path == path and not parsed.query and not parsed.fragment, 'asset_url')


def _read_descriptor(path, ident):
    require(0 < path.stat().st_size <= DESCRIPTOR_CAP, 'descriptor_size')
    descriptor = _json(path.read_bytes())
    _keys(descriptor, {'version', 'lesson_id', 'audio', 'content'})
    require(descriptor['version'] == 'integrated-lecture-output/v2' and descriptor['lesson_id'] == ident,
            'descriptor_scope')
    objects = {}
    for kind in FINAL_OBJECT_CAPS:
        manifest = descriptor[kind]
        require(isinstance(manifest, dict) and isinstance(manifest.get('object_id'), str)
                and re.fullmatch(r'[a-f0-9]{32}', manifest['object_id']), 'descriptor_object_id')
        objects[kind] = f'{ident}.{kind}.{manifest["object_id"]}.bin'
    return descriptor, objects


def validate_final_directory(directory, course, partial=False, *, profile='eager'):
    tag = tag_for(course, True, profile=profile)
    directory = _safe(directory)
    require(directory.is_dir(), 'final_directory')
    files = {p.name: p for p in directory.iterdir()}
    require(files and all(p.is_file() and not p.is_symlink() for p in files.values()), 'final_files')
    descriptors = {}
    allowed = set()
    for name, path in files.items():
        match = re.fullmatch(r'((?:TB|DR|RL|MS)-[0-9]{3})\.descriptor\.json', name)
        if not match:
            continue
        ident = match[1]
        _lesson(ident, course)
        descriptor, objects = _read_descriptor(path, ident)
        try:
            require(all(name in files for name in objects.values()), 'incomplete_lesson_triple')
            require(all(files[name].stat().st_size <= FINAL_OBJECT_CAPS[kind]
                        for kind, name in objects.items()), 'final_cipher_size')
            validate_pack(descriptor['audio'], files[objects['audio']].read_bytes())
            require(descriptor['audio']['lesson_id'] == ident, 'audio_lesson_scope')
            _cipher(descriptor['content'], files[objects['content']].read_bytes(), ident, 'private-metadata')
        except (ValueError, TypeError, KeyError):
            raise TransportError('invalid_lesson_cipher_triple') from None
        descriptors[ident] = path
        allowed.update((name, *objects.values()))
    require(descriptors, 'no_complete_lessons')
    if partial:
        require('index.json' not in files and set(files) == allowed, 'partial_requires_cipher_triples_only')
    else:
        require(set(descriptors) == set(course_ids(course)) and set(files) == allowed | {'index.json'},
                'incomplete_course')
        require(files['index.json'].stat().st_size <= 128 * 1024, 'index_size')
        index = _json(files['index.json'].read_bytes())
        _keys(index, {'version', 'release_tag', 'count', 'lessons'})
        require(index['version'] == 'integrated-lecture-index/v2' and index['release_tag'] == tag
                and type(index['count']) is int and index['count'] == COURSES[course]
                and isinstance(index['lessons'], list) and len(index['lessons']) == COURSES[course], 'index_scope')
        seen = set()
        for row in index['lessons']:
            _keys(row, {'id', 'descriptor_url', 'sha256'})
            ident = row['id']
            require(ident in descriptors and ident not in seen and row['sha256'] == _sha(descriptors[ident]),
                    'index_descriptor_pin')
            _asset_url(row['descriptor_url'], host='github.com',
                       path=f'/{REPOSITORY}/releases/download/{tag}/{ident}.descriptor.json')
            seen.add(ident)
        require(seen == set(course_ids(course)), 'index_coverage')
    # Immutable cipher objects first, descriptors next, the activation index last.
    return sorted(files.values(), key=lambda p: (2 if p.name == 'index.json' else 1 if p.suffix == '.json' else 0, p.name))


class GitHubBackend:
    def __init__(self):
        assert_keyless()
        require(bool(os.environ.get('GITHUB_TOKEN')), 'github_token_missing')
        require(shutil.which('gh') is not None, 'gh_missing')

    def _run(self, args, *, data=None, output=None, missing=False):
        assert_keyless()
        names = ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
                 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')
        env = {k: v for k, v in os.environ.items() if k in names}
        env.update(GITHUB_TOKEN=os.environ['GITHUB_TOKEN'], GH_HOST='github.com', GH_PROMPT_DISABLED='1',
                   GH_NO_UPDATE_NOTIFIER='1', GH_NO_EXTENSION_UPDATE_NOTIFIER='1')
        try:
            result = subprocess.run(['gh', *args], input=data, stdout=output or subprocess.PIPE,
                                    stderr=subprocess.PIPE, env=env, timeout=1200)
        except (OSError, subprocess.SubprocessError):
            raise TransportError('github_transport_failed') from None
        if result.returncode:
            if missing and b'(HTTP 404)' in result.stderr:
                return None
            raise TransportError('github_transport_failed')
        return result.stdout

    def release(self, tag):
        raw = self._run(['api', f'/repos/{REPOSITORY}/releases/tags/{tag}'], missing=True)
        if raw is not None:
            return _json(raw)
        # Drafts may not be returned by the published tag endpoint. The token
        # needs push access; find only the exact expected tag in the bounded list.
        pages = _json(self._run(['api', f'/repos/{REPOSITORY}/releases?per_page=100',
                                '--paginate', '--slurp']))
        require(isinstance(pages, list) and all(isinstance(p, list) for p in pages), 'github_release_pages')
        matches = [release for page in pages for release in page if release.get('tag_name') == tag]
        require(len(matches) <= 1, 'duplicate_release_tag')
        return matches[0] if matches else None

    def create(self, tag):
        payload = json.dumps(dict(tag_name=tag, name=tag, draft=True, prerelease=False)).encode()
        return _json(self._run(['api', '--method', 'POST', f'/repos/{REPOSITORY}/releases', '--input', '-'], data=payload))

    def assets(self, release):
        pages = _json(self._run(['api', f'/repos/{REPOSITORY}/releases/{release["id"]}/assets?per_page=100',
                                '--paginate', '--slurp']))
        require(isinstance(pages, list) and all(isinstance(p, list) for p in pages), 'github_asset_pages')
        return [row for page in pages for row in page]

    def download(self, asset, destination):
        with Path(destination).open('xb') as handle:
            os.chmod(destination, 0o600)
            self._run(['api', f'/repos/{REPOSITORY}/releases/assets/{asset["id"]}',
                       '-H', 'Accept: application/octet-stream'], output=handle)

    def upload(self, release, path):
        # No --clobber. gh performs upload only to this pinned repository/release.
        self._run(['release', 'upload', release['tag_name'], str(Path(path).absolute()), '--repo', REPOSITORY])

    def publish(self, release):
        return _json(self._run(['api', '--method', 'PATCH', f'/repos/{REPOSITORY}/releases/{release["id"]}',
                               '--input', '-'], data=b'{"draft":false}'))


def _release(backend, tag, create=False):
    release = backend.release(tag)
    if release is None and create:
        try:
            release = backend.create(tag)
        except TransportError:
            release = backend.release(tag)  # A concurrent job may have created it.
            require(release is not None, 'release_create_failed')
    if release is None:
        return None
    require(isinstance(release, dict) and release.get('tag_name') == tag and _int(release.get('id'), 1, 2**53-1),
            'release_identity')
    if 'upload_url' in release:
        # gh owns the HTTPS exchange/redirects; still reject an unexpected
        # upload destination advertised in GitHub's release metadata.
        expected = f'https://uploads.github.com/repos/{REPOSITORY}/releases/{release["id"]}/assets{{?name,label}}'
        require(release['upload_url'] == expected, 'release_upload_url')
    return release


def _assets(backend, release):
    # GitHub exposes draft-release assets under a temporary untagged slug.
    # Bind that slug to this exact release's canonical html_url before trusting
    # the browser_download_url. Published assets must use the final tag.
    download_tag = release['tag_name']
    if release.get('draft') and 'html_url' in release:
        prefix = f'https://github.com/{REPOSITORY}/releases/tag/'
        html_url = release['html_url']
        require(isinstance(html_url, str) and html_url.startswith(prefix),
                'release_draft_url')
        slug = html_url[len(prefix):]
        require(slug == download_tag or re.fullmatch(r'untagged-[a-f0-9]{20}', slug),
                'release_draft_url')
        download_tag = slug
    result = {}
    for asset in backend.assets(release):
        require(isinstance(asset, dict) and _int(asset.get('id'), 1, 2**53-1)
                and isinstance(asset.get('name'), str) and Path(asset['name']).name == asset['name']
                and asset['name'] not in result and _int(asset.get('size'), 1, MAX_BUNDLE), 'remote_asset_identity')
        if 'url' in asset:
            _asset_url(asset['url'], host='api.github.com', path=f'/repos/{REPOSITORY}/releases/assets/{asset["id"]}')
        if 'browser_download_url' in asset:
            _asset_url(asset['browser_download_url'], host='github.com',
                       path=f'/{REPOSITORY}/releases/download/{download_tag}/{quote(asset["name"], safe="")}')
        result[asset['name']] = asset
    return result


def _staging_release(backend, course, profile, create=False):
    release = _release(backend, tag_for(course, profile=profile), create=create)
    # The compiled pilot has no publication path, including an already-published
    # release at its isolated tag. Never fall back to an eager or final release.
    if profile == 'compiled' and release is not None:
        require(release.get('draft') is True, 'compiled_release_must_be_draft')
    if profile == 'single_pass' and release is not None:
        require(release.get('draft') is True, 'single_pass_staging_must_be_draft')
    return release


def _staging_assets(backend, release, profile):
    assets = _assets(backend, release)
    if profile == 'compiled':
        for name in assets:
            match = BUNDLE_RE.fullmatch(name)
            require(match is not None and match['lesson'] == COMPILED_LESSON,
                    'compiled_staging_asset_scope')
    if profile == 'single_pass':
        course = release['tag_name'].split('-')[2].upper()
        ids = set(course_ids(course))
        for name in assets:
            match = BUNDLE_RE.fullmatch(name)
            require(match is not None and match['lesson'] in ids,
                    'single_pass_staging_asset_scope')
    return assets


def _same_remote(backend, asset, local):
    require(asset['size'] == Path(local).stat().st_size, 'immutable_asset_collision')
    with tempfile.TemporaryDirectory(prefix='bora-remote-verify-') as temp:
        destination = Path(temp) / 'cipher.asset'
        backend.download(asset, destination)
        require(destination.stat().st_size == asset['size'] and _sha(destination) == _sha(local),
                'immutable_asset_collision')


def _put(backend, release, path, assets, *, profile='eager'):
    asset = assets.get(path.name)
    if asset is not None:
        _same_remote(backend, asset, path)
        return False
    try:
        backend.upload(release, path)
    except TransportError:
        # Resolve a simultaneous immutable upload; never remove or overwrite it.
        current = _staging_assets(backend, release, profile)
        require(path.name in current, 'asset_upload_failed')
        _same_remote(backend, current[path.name], path)
        assets.update(current)
        return False
    assets.update(_staging_assets(backend, release, profile))
    require(path.name in assets, 'asset_upload_missing')
    _same_remote(backend, assets[path.name], path)
    return True


def prepare(course, backend=None, *, profile='eager'):
    assert_keyless()
    _profile(course, profile)
    backend = backend or GitHubBackend()
    release = _staging_release(backend, course, profile, create=True)
    return dict(status='ready', course=course, tag=release['tag_name'])


def upload_directory(directory, course, backend=None, *, profile='eager'):
    assert_keyless()
    _profile(course, profile)
    directory = _safe(directory)
    require(directory.is_dir(), 'upload_directory')
    files = sorted(directory.iterdir())
    require(all(p.is_file() and not p.is_symlink() for p in files), 'upload_files')
    for path in files:
        validate_bundle(path, course, profile=profile)
    if not files:
        return dict(status='nothing_to_upload', uploaded=0, skipped=0)
    backend = backend or GitHubBackend()
    release = _staging_release(backend, course, profile, create=True)
    assets = _staging_assets(backend, release, profile)
    added = sum(_put(backend, release, p, assets, profile=profile) for p in files)
    return dict(status='uploaded', course=course, uploaded=added, skipped=len(files)-added)


def download_lesson(lesson_id, out, course=None, backend=None, *, profile='eager'):
    assert_keyless()
    chosen = _profile_lesson(lesson_id, course, profile)
    backend = backend or GitHubBackend()
    destination = _safe(out)
    require(not destination.exists() or destination.is_dir(), 'download_directory')
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    release = _staging_release(backend, chosen, profile)
    if release is None:
        return dict(status='no_checkpoints', lesson_id=lesson_id, downloaded=0, skipped=0)
    assets = _staging_assets(backend, release, profile)
    selected = []
    for name, asset in sorted(assets.items()):
        match = BUNDLE_RE.fullmatch(name)
        if match and match['lesson'] == lesson_id:
            selected.append(asset)
    downloaded = skipped = 0
    for asset in selected:
        target = _safe(destination / asset['name'])
        if target.exists():
            validate_bundle(target, chosen, profile=profile)
            _same_remote(backend, asset, target)
            skipped += 1
            continue
        with tempfile.TemporaryDirectory(prefix='.download-', dir=destination) as temp:
            local = Path(temp) / asset['name']
            backend.download(asset, local)
            require(local.stat().st_size == asset['size'], 'download_size')
            validate_bundle(local, chosen, profile=profile)
            # Exclusive hardlink commits validated bytes without clobber races.
            try:
                os.link(local, target)
            except FileExistsError:
                require(target.is_file() and not target.is_symlink() and _sha(target) == _sha(local),
                        'local_download_collision')
            else:
                os.chmod(target, 0o600)
                downloaded += 1
    return dict(status='downloaded', lesson_id=lesson_id, downloaded=downloaded, skipped=skipped)


def _final_assets(backend, release, course):
    assets = _assets(backend, release)
    require(type(release.get('draft')) is bool
            and (release['draft'] or 'index.json' in assets), 'final_release_state')
    ids = set(course_ids(course))
    for name in assets:
        if name != 'index.json':
            match = FINAL_ASSET_RE.fullmatch(name)
            require(match is not None and match['lesson'] in ids, 'final_asset_scope')
    return assets


def _fetch_final_object(backend, asset, destination, cap):
    require(asset['size'] <= cap, 'final_asset_size')
    backend.download(asset, destination)
    require(destination.is_file() and not destination.is_symlink()
            and destination.stat().st_size == asset['size'], 'download_size')
    digest = asset.get('digest')
    if digest is not None:
        require(digest == 'sha256:' + _sha(destination), 'final_asset_digest')


def collect_final(course, out, lesson_id=None, backend=None, *, profile='eager'):
    """Recover complete immutable encrypted triples from the single-pass final tag.

    The descriptor is uploaded last per lesson. Uncommitted binaries from an
    interrupted upload are not reused. A corrupt committed triple stops collection.
    """
    assert_keyless()
    require(profile == 'single_pass', 'collect_final_profile_scope')
    ids = course_ids(course)
    if lesson_id is not None:
        _profile_lesson(lesson_id, course, profile)
        ids = [lesson_id]
    destination = _safe(out)
    require(not destination.exists() or destination.is_dir(), 'download_directory')
    backend = backend or GitHubBackend()
    release = _release(backend, tag_for(course, True, profile=profile))
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    if release is None:
        return dict(status='no_completed_lessons', course=course, collected=[])
    assets = _final_assets(backend, release, course)
    collected = []
    for ident in ids:
        name = f'{ident}.descriptor.json'
        if name not in assets:
            continue
        with tempfile.TemporaryDirectory(prefix='.collect-', dir=destination) as temp:
            staging = Path(temp)
            descriptor = staging / name
            _fetch_final_object(backend, assets[name], descriptor, DESCRIPTOR_CAP)
            _, objects = _read_descriptor(descriptor, ident)
            for kind, filename in objects.items():
                require(filename in assets, 'incomplete_lesson_triple')
                _fetch_final_object(backend, assets[filename], staging / filename, FINAL_OBJECT_CAPS[kind])
            files = validate_final_directory(staging, course, partial=True, profile=profile)
            targets = [_safe(destination / path.name) for path in files]
            # Validate any existing local generation before linking missing files.
            for path, target in zip(files, targets):
                if target.exists():
                    require(target.is_file() and _sha(target) == _sha(path), 'local_final_collision')
            for path, target in zip(files, targets):
                try:
                    os.link(path, target)
                except FileExistsError:
                    require(target.is_file() and not target.is_symlink() and _sha(target) == _sha(path),
                            'local_final_collision')
            collected.append(ident)
    return dict(status='collected', course=course, collected=collected)


def publish_directory(directory, course, partial=False, backend=None, *, profile='eager'):
    assert_keyless()
    tag = tag_for(course, True, profile=profile)
    files = validate_final_directory(directory, course, partial, profile=profile)
    backend = backend or GitHubBackend()
    release = _release(backend, tag, create=True)
    assets = _final_assets(backend, release, course) if profile == 'single_pass' else _assets(backend, release)
    require(not partial or 'index.json' not in assets, 'course_already_finalized')
    if profile == 'single_pass':
        if release['draft'] is False:
            require(all(path.name in assets for path in files), 'final_release_missing_assets')
        # Reject a different immutable descriptor before uploading its new audio
        # or content objects. Concurrent jobs may finish distinct lessons.
        for path in files:
            if path.name.endswith('.descriptor.json') and path.name in assets:
                _same_remote(backend, assets[path.name], path)
    # Detect an immutable index conflict before any new upload side effect.
    if not partial and 'index.json' in assets:
        _same_remote(backend, assets['index.json'], Path(directory) / 'index.json')
    added = sum(_put(backend, release, path, assets) for path in files)
    if not partial and release.get('draft'):
        backend.publish(release)
    return dict(status='cipher_lessons_staged' if partial else 'course_published', course=course,
                tag=release['tag_name'], uploaded=added, skipped=len(files)-added,
                activated_index=not partial)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare', 'upload', 'download', 'publish', 'collect-final'):
        sub = commands.add_parser(name)
        sub.add_argument('--course', choices=COURSES, required=name != 'download')
        sub.add_argument('--profile', choices=PROFILES, default='eager')
        if name in ('upload', 'publish'):
            sub.add_argument('--directory', required=True, type=Path)
        if name == 'download':
            sub.add_argument('--lesson', required=True)
        if name == 'collect-final':
            sub.add_argument('--lesson')
        if name in ('download', 'collect-final'):
            sub.add_argument('--out', required=True, type=Path)
        if name == 'publish':
            sub.add_argument('--partial', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare': result = prepare(args.course, profile=args.profile)
        elif args.command == 'upload': result = upload_directory(args.directory, args.course, profile=args.profile)
        elif args.command == 'download': result = download_lesson(args.lesson, args.out, args.course, profile=args.profile)
        elif args.command == 'collect-final': result = collect_final(args.course, args.out, args.lesson, profile=args.profile)
        else: result = publish_directory(args.directory, args.course, args.partial, profile=args.profile)
    except (TransportError, OSError, KeyError, TypeError) as error:
        code = str(error) if isinstance(error, TransportError) else 'transport_failed'
        print(json.dumps(dict(status='failed', reason=code)), file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())

