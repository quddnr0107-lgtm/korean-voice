#!/usr/bin/env python3
"""Reviewed F1 audio: content keys, CPU synthesis, immutable release packs, atomic catalog.

No Cloudflare Container, R2, paid TTS, raw audio Git commits, or Actions artifacts.
The public pack index makes interrupted jobs resumable without individual clip uploads.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, HTTPRedirectHandler, build_opener, urlopen

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'quddnr0107-lgtm/korean-voice'
RECIPE_ID = 'reader-assistance-f1-v1'
VOICE = 'f1'
MEDIA_BASE = 'https://korean-voice.quddnr0107.workers.dev/reader-assistance/f1/v1/'
CATALOG_BRANCH = 'data/reader-assistance-f1-v1'
HEX64 = re.compile(r'^[a-f0-9]{64}$')
FOOTER_MAGIC = b'RAF1PK01'
FOOTER_SIZE = 48  # 8 magic + 8 index length + 32 index SHA-256
MAX_CLIPS = 128
MAX_PACK_BYTES = 20 * 1024 * 1024
MAX_CLIP_BYTES = 2 * 1024 * 1024
MAX_INDEX_BYTES = 1024 * 1024
MAX_RELEASE_ASSETS = 1000
API_VERSION = '2022-11-28'


class ContractError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ContractError(message)


def canonical_json(value):
    """JSON.stringify(recursively key-sorted JSON) for this ASCII-key recipe schema."""
    def normalized(item):
        if isinstance(item, dict):
            require(all(isinstance(key, str) and key.isascii() for key in item), 'Recipe keys must be ASCII strings')
            return {key: normalized(item[key]) for key in sorted(item)}
        if isinstance(item, list):
            return [normalized(value) for value in item]
        if isinstance(item, bool) or item is None or isinstance(item, (str, int)):
            return item
        if isinstance(item, float):
            require(math.isfinite(item), 'Non-finite recipe number')
            if item.is_integer():
                return int(item)
            # The recipe uses fixed decimal parameters, not exponent notation.
            require(0.000001 <= abs(item) < 1000000, 'Recipe number outside canonical decimal range')
            return item
        raise ContractError('Non-JSON recipe value')
    return json.dumps(normalized(value), ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def sha256(data):
    return hashlib.sha256(data.encode('utf-8') if isinstance(data, str) else data).hexdigest()


def git_blob(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode('ascii') + b'\0' + data).hexdigest()


def audio_key(recipe, text):
    require(isinstance(text, str) and bool(text.strip()), 'Empty assistance text')
    return sha256(canonical_json(recipe) + '\n' + sha256(text))


def check_recipe(recipe, verify_sources=False):
    require(isinstance(recipe, dict) and recipe.get('id') == RECIPE_ID, 'Wrong recipe ID')
    require(recipe.get('voice') == VOICE and recipe.get('style') == 'F1', 'Only the selected F1 is permitted')
    expected = {'rate': 1.05, 'baseSpeed': 1, 'steps': 16, 'emotion': 'neutral', 'noProfile': True,
                'seed': 20261105, 'gainDb': -1, 'pitchSt': 0, 'brightDb': 0, 'punch': 0, 'groupChars': 90}
    require(recipe.get('settings') == expected, 'F1 preview settings changed')
    require(recipe.get('encoding') == {'codec': 'libmp3lame', 'quality': 2, 'sampleRate': 44100, 'channels': 1, 'wavSubtype': 'PCM_16'}, 'MP3 encoding recipe changed')
    require(isinstance(recipe.get('assets'), dict) and len(recipe['assets']) == 7, 'Pinned F1 model assets required')
    require(all(HEX64.fullmatch(value or '') for value in recipe['assets'].values()), 'Model asset SHA-256 missing')
    require(recipe.get('helper', {}).get('repository') == 'supertone-oss-archive/supertonic', 'Unexpected helper repository')
    require(recipe.get('model', {}).get('repository') == 'supertone-oss-archive/supertonic-3', 'Unexpected model repository')
    sources = recipe.get('sourceGitBlobs')
    require(isinstance(sources, dict) and {'tools/synth-supertonic.py', 'public/ko-voice.js', 'tools/reader_assistance.py', 'lib/reader-assistance.mjs'} <= set(sources), 'Pinned renderer sources required')
    canonical_json(recipe)
    if verify_sources:
        for relative, expected_blob in sources.items():
            path = (ROOT / relative).resolve()
            require(path.is_relative_to(ROOT) and path.is_file(), 'Missing renderer source: ' + relative)
            require(git_blob(path.read_bytes()) == expected_blob, 'Reviewed renderer source changed: ' + relative)


def build_plan(data_bytes, recipe, fixture=False, allow_partial=False):
    check_recipe(recipe)
    data = json.loads(data_bytes)
    require(isinstance(data, dict) and data.get('schema') == 'reader-assistance.v1', 'Wrong assistance schema')
    entries = data.get('entries')
    require(isinstance(entries, list) and len(entries) > 0, 'No reviewed entries')
    coverage = data.get('coverage', {})
    if not fixture and not allow_partial:
        require(data.get('partial') is False, 'Production baking requires complete reviewed coverage')
        require(coverage.get('bodyRows') == len(entries) == coverage.get('explainedRows'), 'Reviewed body count mismatch')
        require(coverage.get('unexplainedBodyRows') == 0, 'Unreviewed body rows remain')
    elif allow_partial:
        require(coverage.get('explainedRows') == len(entries) and type(coverage.get('bodyRows')) is int and coverage['bodyRows'] >= len(entries), 'Pilot coverage mismatch')
        require(coverage.get('unexplainedBodyRows') == coverage['bodyRows'] - len(entries), 'Pilot missing-row count mismatch')
    rows, tasks, ids = [], {}, set()
    for entry in entries:
        require(isinstance(entry, dict), 'Invalid entry')
        entry_id, text = entry.get('id'), entry.get('text')
        require(isinstance(entry_id, str) and entry_id.strip() and entry_id not in ids, 'Empty or duplicate entry ID')
        require(isinstance(text, str) and 1 <= len(text.strip()) <= 1600, 'Invalid assistance text length: ' + entry_id)
        require(not any(ord(c) < 32 and c not in '\n\t' for c in text), 'Control character in text')
        review = entry.get('review', {})
        digest = sha256(text)
        require(review.get('status') == 'approved' and review.get('textSha256') == digest,
                'Text is not approved at its exact current hash: ' + entry_id)
        key = audio_key(recipe, text)
        ids.add(entry_id)
        rows.append({'id': entry_id, 'audioKey': key})
        tasks[key] = {'audioKey': key, 'text': text, 'textSha256': digest, 'bucket': key[0]}
    complete = not fixture and data.get('partial') is False and coverage.get('bodyRows') == len(entries) == coverage.get('explainedRows') and coverage.get('unexplainedBodyRows') == 0
    return {'schema': 'reader-assistance-bake-plan.v1', 'fixture': fixture, 'completeEligible': complete,
            'sourceDataSha256': sha256(data_bytes), 'recipeSha256': sha256(canonical_json(recipe)),
            'recipe': RECIPE_ID, 'voice': VOICE, 'expectedEntries': len(entries),
            'entries': rows, 'tasks': dict(sorted(tasks.items()))}


def checked_clip(key, clip, pack_size=None, index_start=None):
    require(isinstance(key, str) and HEX64.fullmatch(key) is not None, 'Invalid audio key')
    require(isinstance(clip, dict), 'Invalid clip')
    for name in ('sha256', 'textSha256'):
        require(isinstance(clip.get(name), str) and HEX64.fullmatch(clip[name]) is not None, 'Invalid clip ' + name)
    for name in ('offset', 'length', 'durationMs'):
        require(type(clip.get(name)) is int, 'Invalid clip integer ' + name)
    require(clip['offset'] >= 0 and 128 <= clip['length'] <= MAX_CLIP_BYTES, 'Invalid clip range')
    require(500 <= clip['durationMs'] <= 180000, 'Invalid clip duration')
    if pack_size is not None:
        require(clip['offset'] + clip['length'] <= pack_size, 'Clip beyond pack')
    if index_start is not None:
        require(clip['offset'] + clip['length'] <= index_start, 'Clip overlaps index')
    return clip


def build_pack(clips, recipe_digest, source_digest, fixture=False):
    """clips: [(audioKey, metadata, exact MP3 bytes)]; MP3 bytes are never rewritten."""
    require(1 <= len(clips) <= MAX_CLIPS, 'Pack clip count outside limit')
    require(HEX64.fullmatch(recipe_digest) and HEX64.fullmatch(source_digest), 'Missing pack provenance')
    body, index_clips = bytearray(), {}
    for key, metadata, mp3 in clips:
        require(key not in index_clips, 'Duplicate audio key in pack')
        require(sha256(mp3) == metadata.get('sha256'), 'MP3 checksum changed before packing')
        item = {'offset': len(body), 'length': len(mp3), 'sha256': metadata['sha256'],
                'durationMs': metadata['durationMs'], 'textSha256': metadata['textSha256']}
        checked_clip(key, item)
        index_clips[key] = item
        body.extend(mp3)
    index = {'schema': 'reader-assistance-pack.v1', 'voice': VOICE, 'recipe': RECIPE_ID,
             'recipeSha256': recipe_digest, 'sourceDataSha256': source_digest, 'fixture': fixture,
             'clips': index_clips}
    payload = canonical_json(index).encode('utf-8')
    require(len(payload) <= MAX_INDEX_BYTES, 'Pack index too large')
    body.extend(payload)
    body.extend(FOOTER_MAGIC + struct.pack('>Q', len(payload)) + hashlib.sha256(payload).digest())
    require(len(body) <= MAX_PACK_BYTES, 'Pack byte limit exceeded')
    packed = bytes(body)
    return packed, index, sha256(packed)


def parse_footer(footer, pack_size):
    require(len(footer) == FOOTER_SIZE and footer[:8] == FOOTER_MAGIC, 'Invalid pack footer')
    length = struct.unpack('>Q', footer[8:16])[0]
    require(1 <= length <= MAX_INDEX_BYTES and length + FOOTER_SIZE < pack_size, 'Invalid pack index length')
    return pack_size - FOOTER_SIZE - length, length, footer[16:].hex()


def parse_index(payload, expected_sha, index_start, pack_size, recipe_digest, fixture=False):
    require(sha256(payload) == expected_sha, 'Pack index checksum mismatch')
    item = json.loads(payload)
    require(item.get('schema') == 'reader-assistance-pack.v1' and item.get('recipe') == RECIPE_ID and item.get('voice') == VOICE,
            'Pack schema or selected voice mismatch')
    require(item.get('recipeSha256') == recipe_digest and item.get('fixture') is fixture, 'Pack recipe or fixture namespace mismatch')
    clips = item.get('clips')
    require(isinstance(clips, dict) and 1 <= len(clips) <= MAX_CLIPS, 'Invalid pack clip count')
    intervals = []
    for key, clip in clips.items():
        checked_clip(key, clip, pack_size, index_start)
        intervals.append((clip['offset'], clip['offset'] + clip['length']))
    intervals.sort()
    require(intervals[0][0] == 0 and intervals[-1][1] == index_start, 'Pack body coverage mismatch')
    require(all(a[1] == b[0] for a, b in zip(intervals, intervals[1:])), 'Pack clip overlap or gap')
    return item


class RangeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        require(parsed.scheme == 'https' and parsed.hostname == 'release-assets.githubusercontent.com' and not parsed.username
                and parsed.port in (None, 443), 'Release redirected outside the expected asset host')
        count = getattr(req, 'redirect_count', 0) + 1
        require(count <= 3, 'Too many asset redirects')
        follow = Request(newurl, headers={'Range': req.get_header('Range'), 'Accept-Encoding': 'identity', 'User-Agent': 'reader-assistance-f1'})
        follow.redirect_count = count
        return follow


def public_range(url, start, length, total):
    parsed = urlparse(url)
    require(parsed.scheme == 'https' and parsed.hostname == 'github.com' and parsed.port in (None, 443)
            and parsed.path.startswith('/' + REPOSITORY + '/releases/download/') and not parsed.query and not parsed.fragment,
            'Unexpected public pack URL')
    require(type(start) is int and type(length) is int and 0 <= start < total and 1 <= length <= MAX_INDEX_BYTES and start + length <= total,
            'Invalid requested pack range')
    request = Request(url, headers={'Range': f'bytes={start}-{start + length - 1}', 'Accept-Encoding': 'identity', 'User-Agent': 'reader-assistance-f1'})
    with build_opener(RangeRedirect()).open(request, timeout=60) as response:
        require(response.status == 206, 'Public release does not honor Range with HTTP 206')
        require(response.headers.get('Content-Range') == f'bytes {start}-{start + length - 1}/{total}', 'Incorrect public Content-Range')
        require(response.headers.get('Content-Encoding', 'identity') == 'identity', 'Encoded range response')
        require(response.headers.get('Content-Length') == str(length), 'Incorrect public Content-Length')
        data = response.read(length + 1)
        require(len(data) == length, 'Truncated or oversized public range')
        return data


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ContractError('Unexpected authenticated GitHub API redirect')


class GitHub:
    def __init__(self, token=None):
        self.token = token or os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
        require(bool(self.token), 'GITHUB_TOKEN is required for release operations')
        self.opener = build_opener(NoRedirect())
        self.next_mutation = 0

    def request(self, method, path, value=None, binary=None, content_type=None, allow404=False):
        url = path if path.startswith('https://') else 'https://api.github.com' + path
        host = urlparse(url).hostname
        require(host in ('api.github.com', 'uploads.github.com') and urlparse(url).scheme == 'https', 'Unexpected GitHub API host')
        require(binary is None or value is None, 'Ambiguous API payload')
        payload = binary if binary is not None else (json.dumps(value, ensure_ascii=False).encode() if value is not None else None)
        headers = {'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
                   'X-GitHub-Api-Version': API_VERSION, 'User-Agent': 'reader-assistance-f1'}
        if payload is not None:
            headers['Content-Type'] = content_type or 'application/json'
        for attempt in range(5):
            if method != 'GET':
                delay = self.next_mutation - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                self.next_mutation = time.monotonic() + 1.1
            try:
                with self.opener.open(Request(url, data=payload, headers=headers, method=method), timeout=90) as response:
                    return json.loads(response.read())
            except HTTPError as error:
                if error.code == 404 and allow404:
                    return None
                response = error.read(4096)
                if error.code in (403, 429):
                    retry = error.headers.get('Retry-After')
                    remaining = error.headers.get('X-RateLimit-Remaining')
                    if retry and retry.isdigit():
                        delay = int(retry) + 1
                    elif remaining == '0':
                        delay = max(1, int(error.headers.get('X-RateLimit-Reset', '0')) - time.time() + 2)
                    elif b'secondary rate limit' in response.lower():
                        delay = 60 * (2 ** attempt)
                    else:
                        raise ContractError(f'GitHub permission or API failure: HTTP {error.code}') from error
                    require(delay <= 3700 and attempt < 4, 'GitHub rate limit retry exhausted')
                    print(f'GitHub rate limit: waiting {math.ceil(delay)} seconds', flush=True)
                    time.sleep(delay)
                    continue
                # Mutating request outcomes can be unknown; never repeat them here.
                raise ContractError(f'GitHub API failure: HTTP {error.code}') from error
            except (URLError, TimeoutError) as error:
                raise ContractError('GitHub request failed; rerun to reconcile immutable assets') from error
        raise ContractError('GitHub request exhausted')

    def release(self, tag, create=False):
        prefix = '/repos/' + REPOSITORY + '/releases'
        found = self.request('GET', prefix + '/tags/' + quote(tag, safe=''), allow404=True)
        if found is None and create:
            require(re.fullmatch(r'reader-assistance-f1-v1(?:-fixture)?-packs', tag) is not None, 'Unexpected release namespace')
            sha = os.environ.get('GITHUB_SHA', '')
            require(re.fullmatch(r'[a-f0-9]{40}', sha) is not None, 'Pinned workflow commit required for publication')
            try:
                found = self.request('POST', prefix, {'tag_name': tag, 'target_commitish': sha, 'name': tag,
                                                    'body': 'Public F1 assistance audio packs. Immutable content-addressed assets; do not replace files.',
                                                    'draft': False, 'prerelease': True, 'make_latest': 'false'})
            except ContractError:
                # A concurrent bucket can have created exactly this release. Reconcile with a read;
                # never repeat a potentially successful mutating request or replace a release.
                found = self.request('GET', prefix + '/tags/' + quote(tag, safe=''), allow404=True)
                if found is None:
                    raise
        if found is not None:
            require(found.get('tag_name') == tag and found.get('draft') is False, 'Unexpected release state')
        return found

    def assets(self, release_id):
        output = []
        for page in range(1, 12):
            items = self.request('GET', f'/repos/{REPOSITORY}/releases/{release_id}/assets?per_page=100&page={page}')
            require(isinstance(items, list), 'Invalid release asset list')
            output.extend(items)
            if len(items) < 100:
                require(len(output) <= MAX_RELEASE_ASSETS, 'Release asset capacity exceeded')
                return output
        raise ContractError('Release asset pagination did not terminate')

    def upload(self, release, packed, digest, bucket):
        require(sha256(packed) == digest, 'Pack checksum changed before upload')
        require(not release.get('immutable'), 'Release is immutable; cannot add the missing pack')
        require(bucket in '0123456789abcdef' and len(bucket) == 1, 'Invalid pack bucket')
        query = urlencode({'name': digest + '.bin', 'label': 'reader-assistance-pack.v1/' + bucket})
        url = f'https://uploads.github.com/repos/{REPOSITORY}/releases/{release["id"]}/assets?{query}'
        asset = self.request('POST', url, binary=packed, content_type='application/octet-stream')
        validate_asset(asset, release['tag_name'])
        require(asset.get('digest') == 'sha256:' + digest and asset.get('size') == len(packed), 'Uploaded pack digest or size mismatch')
        return asset


def validate_asset(asset, tag):
    require(isinstance(asset, dict) and isinstance(asset.get('name'), str), 'Invalid release asset')
    match = re.fullmatch(r'([a-f0-9]{64})\.bin', asset['name'])
    require(match is not None and asset.get('state') == 'uploaded', 'Unexpected release asset name or state')
    digest = match.group(1)
    require(asset.get('digest') == 'sha256:' + digest, 'GitHub asset digest does not match its immutable name')
    require(re.fullmatch(r'reader-assistance-pack\.v1/[a-f0-9]', asset.get('label') or '') is not None, 'Invalid pack bucket label')
    require(type(asset.get('size')) is int and FOOTER_SIZE < asset['size'] <= MAX_PACK_BYTES, 'Invalid pack size')
    expected_url = f'https://github.com/{REPOSITORY}/releases/download/{tag}/{digest}.bin'
    require(asset.get('browser_download_url') == expected_url, 'Unexpected pack download URL')
    return digest


def read_remote_index(asset, recipe_digest, fixture=False, fetch_range=public_range):
    size, url = asset['size'], asset['browser_download_url']
    footer = fetch_range(url, size - FOOTER_SIZE, FOOTER_SIZE, size)
    start, length, digest = parse_footer(footer, size)
    payload = fetch_range(url, start, length, size)
    return parse_index(payload, digest, start, size, recipe_digest, fixture)


def inventory(client, plan, release=None, cache_dir=None, bucket=None):
    tag = RECIPE_ID + ('-fixture' if plan['fixture'] else '') + '-packs'
    release = release if release is not None else client.release(tag)
    packs, clips = {}, {}
    if release is None:
        return {'release': None, 'packs': packs, 'clips': clips, 'assetCount': 0}
    assets = client.assets(release['id'])
    for asset in assets:
        digest = validate_asset(asset, tag)
        if bucket is not None and asset['label'][-1] != bucket:
            continue
        # Local index caches are disposable accelerators and carry the authoritative asset digest.
        cache = Path(cache_dir) / (digest + '.json') if cache_dir else None
        index = None
        if cache and cache.is_file():
            saved = json.loads(cache.read_text())
            if saved.get('assetDigest') == asset['digest'] and saved.get('assetSize') == asset['size']:
                index = saved.get('index')
        if index is None:
            index = read_remote_index(asset, plan['recipeSha256'], plan['fixture'])
            if cache:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps({'assetDigest': asset['digest'], 'assetSize': asset['size'], 'index': index}, ensure_ascii=False))
        require(index.get('recipeSha256') == plan['recipeSha256'] and index.get('fixture') is plan['fixture'], 'Cached pack recipe mismatch')
        require(all(key[0] == asset['label'][-1] for key in index.get('clips', {})), 'Pack bucket label does not match its keys')
        packs[digest] = {'url': asset['browser_download_url'], 'size': asset['size'], 'sha256': digest}
        for key, value in index['clips'].items():
            checked_clip(key, value, asset['size'])
            clip = {**value, 'pack': digest}
            if key in clips:
                require({k: v for k, v in clips[key].items() if k not in ('offset', 'pack')} ==
                        {k: v for k, v in clip.items() if k not in ('offset', 'pack')}, 'Conflicting immutable audio identity')
                continue
            clips[key] = clip
    for key, task in plan['tasks'].items():
        if key in clips:
            require(clips[key]['textSha256'] == task['textSha256'], 'Existing pack belongs to different text')
    return {'release': release, 'packs': packs, 'clips': clips, 'assetCount': len(assets)}


def download_model(recipe, assets_dir):
    check_recipe(recipe, verify_sources=True)
    base = Path(assets_dir)
    for relative, expected in recipe['assets'].items():
        require(re.fullmatch(r'(onnx|voice_styles)/[A-Za-z0-9_.-]+', relative) is not None, 'Invalid model asset path')
        path = base / relative
        if path.is_file() and sha256(path.read_bytes()) == expected:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f'https://huggingface.co/{recipe["model"]["repository"]}/resolve/{recipe["model"]["revision"]}/{relative}'
        with urlopen(Request(url, headers={'User-Agent': 'reader-assistance-f1'}), timeout=180) as response:
            require(response.status == 200, 'Model download failed')
            data = response.read(512 * 1024 * 1024 + 1)
        require(len(data) <= 512 * 1024 * 1024 and sha256(data) == expected, 'Model checksum mismatch: ' + relative)
        path.write_bytes(data)
    helper = recipe['helper']
    helper_path = base / 'reader-assistance-helper.py'
    if not helper_path.is_file() or git_blob(helper_path.read_bytes()) != helper['gitBlob']:
        url = f'https://raw.githubusercontent.com/{helper["repository"]}/{helper["commit"]}/{helper["path"]}'
        with urlopen(url, timeout=60) as response:
            data = response.read(1024 * 1024 + 1)
        require(len(data) <= 1024 * 1024 and git_blob(data) == helper['gitBlob'], 'Pinned helper checksum mismatch')
        helper_path.write_bytes(data)
    return helper_path


def import_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def validate_wave(wave, sample_rate, text, decoded=False):
    import numpy as np
    require(sample_rate == 44100 and wave.ndim == 1 and len(wave) > 0, 'Invalid mono F1 sample rate')
    require(np.isfinite(wave).all(), 'Non-finite synthesized audio')
    duration = len(wave) / sample_rate
    require(0.5 <= duration <= min(180, max(12, len(text) * 0.8)), 'Audio duration outside text-based bound')
    peak = float(np.max(np.abs(wave)))
    rms = float(np.sqrt(np.mean(np.square(wave.astype(np.float64)))))
    require(0.01 <= peak <= (1.0 if decoded else 0.9) and rms >= 0.001, 'Silent or clipped audio')
    require(float(np.mean(np.abs(wave) >= 0.999)) <= 0.0001, 'Sustained clipping')
    frame = 441
    frames = wave[:len(wave) // frame * frame].reshape(-1, frame)
    energy = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    audible = np.flatnonzero(energy >= 0.001)
    require(len(audible) > 0, 'No audible frames')
    require(audible[0] <= 150 and len(energy) - audible[-1] <= 150, 'Excessive leading/trailing silence')
    silent_run = longest = 0
    for active in energy[audible[0]:audible[-1] + 1] >= 0.001:
        silent_run = 0 if active else silent_run + 1
        longest = max(longest, silent_run)
    require(longest <= 300, 'Excessive internal silence')
    return {'durationMs': round(duration * 1000), 'sampleRate': sample_rate, 'peak': round(peak, 6), 'rms': round(rms, 6)}


class Renderer:
    def __init__(self, recipe, assets_dir):
        check_recipe(recipe, verify_sources=True)
        helper_path = download_model(recipe, assets_dir)
        import numpy as np
        import onnxruntime as ort
        import soundfile as sf
        import imageio_ffmpeg
        ort.disable_telemetry_events()
        self.np, self.sf = np, sf
        self.ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        self.recipe = recipe
        self.synth = import_path('reader_assistance_synth', ROOT / 'tools/synth-supertonic.py')
        helper = import_path('reader_assistance_helper', helper_path)
        self.tts = helper.load_text_to_speech(str(Path(assets_dir) / 'onnx'), False)
        self.style, _ = self.synth.load_blend(helper, str(Path(assets_dir) / 'voice_styles'), 'F1')

    def render(self, task):
        np, S, settings = self.np, self.synth, self.recipe['settings']
        np.random.seed(settings['seed'])
        units = []
        for paragraph in [line for line in task['text'].split('\n') if line.strip()]:
            plan = S.plan(paragraph, emotion='neutral', profile=None, rate=1.05)
            part = S.units_from_plan(plan)
            if part:
                part[-1]['hard'] = True
                part[-1]['pause_ms'] = max(part[-1]['pause_ms'], 600)
            units.extend(part)
        units = S.group_units(units, max_chars=90)
        require(bool(units), 'Speech planner produced no units')
        pieces = []
        for unit in units:
            speed = float(np.clip(unit['rate'] * (0.95 if unit['emph'] else 1.0), 0.7, 1.6))
            wave, _ = self.tts._infer([unit['text']], ['ko'], self.style, 16, speed)
            part = S.fade(S.trim(np.asarray(wave, dtype=np.float32).reshape(-1), 44100), 44100)
            pieces.append(part)
            pieces.append(np.zeros(int(44100 * unit['pause_ms'] / 1000), dtype=np.float32))
        wave = np.concatenate(pieces)
        require(np.isfinite(wave).all() and float(np.max(np.abs(wave))) > 1e-5, 'Invalid raw F1 output')
        wave = wave / np.max(np.abs(wave)) * (10 ** (-1 / 20))
        metrics = validate_wave(wave, 44100, task['text'])
        with tempfile.TemporaryDirectory(prefix='assistance-f1-') as temporary:
            wav_path, mp3_path = Path(temporary) / 'clip.wav', Path(temporary) / 'clip.mp3'
            self.sf.write(wav_path, wave, 44100, subtype='PCM_16')
            subprocess.run([self.ffmpeg, '-v', 'error', '-y', '-i', str(wav_path), '-codec:a', 'libmp3lame', '-q:a', '2', str(mp3_path)], check=True, timeout=60)
            mp3 = mp3_path.read_bytes()
            require(128 <= len(mp3) <= MAX_CLIP_BYTES, 'Encoded MP3 size outside limit')
            decoded = subprocess.run([self.ffmpeg, '-v', 'error', '-i', str(mp3_path), '-f', 'f32le', '-acodec', 'pcm_f32le', '-ar', '44100', '-ac', '1', '-'],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=60).stdout
            checked = validate_wave(np.frombuffer(decoded, dtype='<f4'), 44100, task['text'], decoded=True)
            require(abs(checked['durationMs'] - metrics['durationMs']) <= 100, 'MP3 decoding duration changed')
        return mp3, {'sha256': sha256(mp3), 'textSha256': task['textSha256'], 'durationMs': checked['durationMs']}


def bake(client, plan, recipe, bucket, assets_dir, cache_dir=None, renderer_factory=Renderer):
    require(bucket in '0123456789abcdef' and len(bucket) == 1, 'Invalid bake bucket')
    state = inventory(client, plan, cache_dir=cache_dir, bucket=bucket)
    tasks = [task for key, task in plan['tasks'].items() if task['bucket'] == bucket and key not in state['clips']]
    print(json.dumps({'bucket': bucket, 'missing': len(tasks), 'reused': sum(1 for task in plan['tasks'].values() if task['bucket'] == bucket) - len(tasks)}), flush=True)
    if not tasks:
        return {'baked': 0, 'packs': 0}
    tag = RECIPE_ID + ('-fixture' if plan['fixture'] else '') + '-packs'
    release = state['release'] or client.release(tag, create=True)
    require(state['assetCount'] + math.ceil(len(tasks) / MAX_CLIPS) + 16 < MAX_RELEASE_ASSETS, 'Release is near capacity; do not overwrite assets')
    renderer = renderer_factory(recipe, assets_dir)
    pending, pending_bytes, baked, uploaded = [], 0, 0, 0

    def flush():
        nonlocal pending, pending_bytes, uploaded
        if not pending:
            return
        packed, index, digest = build_pack(pending, plan['recipeSha256'], plan['sourceDataSha256'], plan['fixture'])
        existing = next((asset for asset in client.assets(release['id']) if asset.get('name') == digest + '.bin'), None)
        asset = existing or client.upload(release, packed, digest, bucket)
        validate_asset(asset, tag)
        require(asset['digest'] == 'sha256:' + digest and asset['size'] == len(packed), 'Existing immutable pack conflicts')
        # The first real upload also proves GitHub's redirect/range path before further publication.
        remote_index = read_remote_index(asset, plan['recipeSha256'], plan['fixture'])
        require(canonical_json(remote_index) == canonical_json(index), 'Published pack index differs from local build')
        print(json.dumps({'bucket': bucket, 'publishedPack': digest, 'clips': len(pending), 'bytes': len(packed)}), flush=True)
        pending, pending_bytes = [], 0
        uploaded += 1

    for number, task in enumerate(tasks, 1):
        mp3, metadata = renderer.render(task)
        if pending and (len(pending) >= MAX_CLIPS or pending_bytes + len(mp3) + MAX_INDEX_BYTES + FOOTER_SIZE > MAX_PACK_BYTES):
            flush()
        pending.append((task['audioKey'], metadata, mp3))
        pending_bytes += len(mp3)
        baked += 1
        print(json.dumps({'bucket': bucket, 'rendered': number, 'totalMissing': len(tasks), 'audioKey': task['audioKey'], 'durationMs': metadata['durationMs']}), flush=True)
    flush()
    return {'baked': baked, 'packs': uploaded}


def assemble(plan, state):
    missing = set(plan['tasks']) - set(state['clips'])
    require(not missing, 'Audio remains missing: ' + str(len(missing)))
    clips = {key: state['clips'][key] for key in plan['tasks']}
    for key, clip in clips.items():
        checked_clip(key, clip)
        require(clip['textSha256'] == plan['tasks'][key]['textSha256'], 'Catalog text mismatch')
    used = {clip['pack'] for clip in clips.values()}
    packs = {key: state['packs'][key] for key in sorted(used)}
    return {'schema': 'reader-assistance-audio-pack.v1', 'complete': plan['completeEligible'],
            'voice': VOICE, 'recipe': RECIPE_ID, 'sourceDataSha256': plan['sourceDataSha256'],
            'recipeSha256': plan['recipeSha256'], 'expectedEntries': plan['expectedEntries'],
            'readyEntries': len(plan['entries']), 'packs': packs, 'clips': clips, 'entries': plan['entries']}


def attach(data_bytes, recipe, catalog):
    plan = build_plan(data_bytes, recipe)
    require(catalog.get('schema') == 'reader-assistance-audio-pack.v1' and catalog.get('complete') is True,
            'Only a complete production catalog can be attached')
    for key in ('voice', 'recipe', 'sourceDataSha256', 'recipeSha256', 'expectedEntries'):
        require(catalog.get(key) == plan[key], 'Catalog provenance mismatch: ' + key)
    require(catalog.get('readyEntries') == plan['expectedEntries'] and catalog.get('entries') == plan['entries'], 'Catalog entry coverage mismatch')
    require(set(catalog.get('clips', {})) == set(plan['tasks']), 'Catalog clip set mismatch')
    output = json.loads(data_bytes)
    for entry, binding in zip(output['entries'], plan['entries']):
        key = binding['audioKey']
        clip = checked_clip(key, catalog['clips'][key])
        require(clip['textSha256'] == sha256(entry['text']), 'Audio does not match exact current text')
        entry['audio'] = {'voice': VOICE, 'recipe': RECIPE_ID, 'url': MEDIA_BASE + key + '.mp3',
                          'durationMs': clip['durationMs'], 'sha256': clip['sha256'], 'audioKey': key,
                          'textSha256': clip['textSha256']}
    output['audio'] = {'voice': VOICE, 'recipe': RECIPE_ID, 'complete': True, 'recipeSha256': plan['recipeSha256'],
                       'sourceDataSha256': plan['sourceDataSha256'], 'readyEntries': plan['expectedEntries']}
    return output


def publish_catalog(client, catalog, allow_pilot=False):
    require(catalog.get('complete') is True or (allow_pilot and catalog.get('complete') is False), 'Fixture or partial catalog cannot be published to the production data branch')
    branch = CATALOG_BRANCH if catalog.get('complete') is True else CATALOG_BRANCH + '-pilot'
    data = (json.dumps(catalog, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
    prefix = '/repos/' + REPOSITORY
    ref_path = prefix + '/git/ref/heads/' + quote(branch, safe='/')
    ref = client.request('GET', ref_path, allow404=True)
    parent_sha = ref['object']['sha'] if ref else os.environ.get('GITHUB_SHA', '')
    require(re.fullmatch(r'[a-f0-9]{40}', parent_sha) is not None, 'Pinned catalog parent required')
    parent = client.request('GET', prefix + '/git/commits/' + parent_sha)
    blob = client.request('POST', prefix + '/git/blobs', {'content': base64.b64encode(data).decode('ascii'), 'encoding': 'base64'})
    require(blob.get('sha') == git_blob(data), 'Catalog Git blob checksum mismatch')
    tree = client.request('POST', prefix + '/git/trees', {'base_tree': parent['tree']['sha'], 'tree': [
        {'path': 'reader-assistance/audio-manifest.json', 'mode': '100644', 'type': 'blob', 'sha': blob['sha']}]})
    if tree.get('sha') == parent['tree']['sha']:
        return {'branch': branch, 'commit': parent_sha, 'changed': False, 'blob': blob['sha']}
    commit = client.request('POST', prefix + '/git/commits', {'message': 'Publish ' + ('complete' if catalog['complete'] else 'pilot-only') + ' F1 assistance audio catalog ' + catalog['sourceDataSha256'][:16],
                                                           'tree': tree['sha'], 'parents': [parent_sha]})
    if ref:
        latest = client.request('GET', ref_path)
        require(latest['object']['sha'] == parent_sha, 'Catalog branch changed; refusing to overwrite it')
        client.request('PATCH', prefix + '/git/refs/heads/' + quote(branch, safe='/'), {'sha': commit['sha'], 'force': False})
    else:
        client.request('POST', prefix + '/git/refs', {'ref': 'refs/heads/' + branch, 'sha': commit['sha']})
    return {'branch': branch, 'commit': commit['sha'], 'changed': True, 'blob': blob['sha']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['plan', 'inventory', 'download-model', 'bake', 'assemble', 'attach'])
    parser.add_argument('--data', default='reader-assistance/data.json')
    parser.add_argument('--recipe', default='reader-assistance/recipe.json')
    parser.add_argument('--assets', default='supertonic3')
    parser.add_argument('--bucket')
    parser.add_argument('--catalog')
    parser.add_argument('--out')
    parser.add_argument('--fixture', action='store_true', help='Separate non-production release namespace; cannot attach or publish a complete catalog')
    parser.add_argument('--pilot', action='store_true', help='Only approved real entries; incomplete coverage remains incomplete and cannot activate the production catalog')
    parser.add_argument('--publish-catalog', action='store_true')
    args = parser.parse_args()
    recipe = json.loads(Path(args.recipe).read_text())
    check_recipe(recipe, verify_sources=True)
    if args.command == 'download-model':
        download_model(recipe, args.assets)
        print('Pinned F1 model and helper verified.')
        return
    data_bytes = Path(args.data).read_bytes()
    require(not (args.fixture and args.pilot), 'Choose fixture or real pilot, not both')
    plan = build_plan(data_bytes, recipe, args.fixture, args.pilot)
    if args.command == 'plan':
        summary = {key: plan[key] for key in ('fixture', 'sourceDataSha256', 'recipeSha256', 'expectedEntries')}
        summary['uniqueAudioClips'] = len(plan['tasks'])
        summary['buckets'] = {bucket: sum(task['bucket'] == bucket for task in plan['tasks'].values()) for bucket in '0123456789abcdef'}
        result = plan
        print(json.dumps(summary, ensure_ascii=False))
    elif args.command == 'attach':
        require(not args.fixture and not args.pilot and args.catalog, 'Production catalog file is required')
        result = attach(data_bytes, recipe, json.loads(Path(args.catalog).read_text()))
    else:
        require(os.environ.get('GITHUB_REPOSITORY', REPOSITORY) == REPOSITORY, 'Unexpected repository')
        client = GitHub()
        cache = Path(os.environ.get('RUNNER_TEMP', tempfile.gettempdir())) / 'reader-assistance-pack-indexes'
        if args.command == 'bake':
            result = bake(client, plan, recipe, args.bucket, args.assets, cache_dir=cache)
        else:
            state = inventory(client, plan, cache_dir=cache)
            if args.command == 'inventory':
                require(args.bucket is None or (args.bucket in '0123456789abcdef' and len(args.bucket) == 1), 'Invalid bucket')
                counts = {bucket: sum(task['bucket'] == bucket and key not in state['clips'] for key, task in plan['tasks'].items()) for bucket in '0123456789abcdef'}
                missing = counts[args.bucket] if args.bucket else sum(counts.values())
                buckets = [bucket for bucket, count in counts.items() if count]
                result = {'bucket': args.bucket, 'missing': missing, 'buckets': buckets, 'readyUniqueClips': len(set(plan['tasks']) & set(state['clips']))}
                output_path = os.environ.get('GITHUB_OUTPUT')
                if output_path:
                    with open(output_path, 'a') as out:
                        out.write('missing=' + str(missing) + '\n')
                        out.write('buckets=' + json.dumps(buckets or ['0'], separators=(',', ':')) + '\n')
            else:
                result = assemble(plan, state)
                if args.publish_catalog:
                    require(not args.fixture, 'Test fixtures cannot publish a catalog branch')
                    publication = publish_catalog(client, result, allow_pilot=args.pilot)
                    print(json.dumps(publication), flush=True)
        if args.command != 'assemble':
            print(json.dumps(result, ensure_ascii=False), flush=True)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    try:
        main()
    except (ContractError, json.JSONDecodeError, OSError, subprocess.SubprocessError) as error:
        print('reader-assistance: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
