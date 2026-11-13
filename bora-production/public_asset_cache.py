"""Keyless cache of allowlisted public bytes, authenticated by repository pins.

The cache contains no HF metadata, credentials, dependencies or private output.
Runtime HF metadata is regenerated only after every asset matches its SHA256.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile

import download_models as pins

MANIFEST = Path(__file__).with_name('pinned-public-assets.json')
HASH = re.compile(r'[0-9a-f]{64}')
MODELS = {'qwen': (pins.MODEL_REPOSITORY, pins.MODEL_REVISION, pins.QWEN_REQUIRED),
          'asr': (pins.ASR_REPOSITORY, pins.ASR_REVISION, pins.ASR_REQUIRED)}


class CacheError(ValueError):
    pass


def check(ok, code):
    if not ok:
        raise CacheError(code)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def load_manifest(path=MANIFEST):
    data = json.loads(Path(path).read_text())
    check(data.get('schema') == 'bora-public-assets/v1'
          and set(data.get('models', {})) == set(MODELS), 'asset_manifest_schema')
    for name, (repository, revision, required) in MODELS.items():
        model = data['models'][name]
        check(model.get('repository') == repository and model.get('revision') == revision
              and set(model.get('files', {})) == set(required), 'asset_manifest_pins')
        for filename, entry in model['files'].items():
            path = PurePosixPath(filename)
            check(not path.is_absolute() and '..' not in path.parts
                  and str(path) == filename and '\\' not in filename, 'asset_manifest_path')
            check(set(entry) == {'bytes', 'sha256'} and type(entry['bytes']) is int
                  and 0 < entry['bytes'] <= 4 * 1024**3
                  and isinstance(entry['sha256'], str) and HASH.fullmatch(entry['sha256'])
                  and (required[filename] is None or entry['bytes'] == required[filename]),
                  'asset_manifest_file')
    return data


def no_symlink_parents(path, *, leaf=True):
    path = Path(path).absolute()
    for item in ([path] if leaf else []) + list(path.parents):
        check(not item.is_symlink(), 'asset_symlink')


def check_tree(root, allowed):
    no_symlink_parents(root)
    check(root.is_dir(), 'asset_directory_missing')
    actual = set()
    allowed_dirs = {str(parent) for name in allowed for parent in PurePosixPath(name).parents
                    if str(parent) != '.'}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs:
            path = Path(directory) / name
            check(not path.is_symlink() and str(path.relative_to(root)) in allowed_dirs,
                  'asset_unexpected_directory')
        for name in files:
            path = Path(directory) / name
            check(not path.is_symlink() and path.is_file(), 'asset_file_type')
            actual.add(path.relative_to(root).as_posix())
    check(actual == set(allowed), 'asset_file_set')


def verify(root, manifest, *, runtime=False, exact=True):
    root = Path(root)
    no_symlink_parents(root)
    allowed = set()
    for model_name, model in manifest['models'].items():
        for filename, entry in model['files'].items():
            relative = f'{model_name}/{filename}'
            allowed.add(relative)
            path = root / relative
            no_symlink_parents(path)
            check(path.is_file() and path.stat().st_size == entry['bytes'], 'asset_size')
            check(digest(path) == entry['sha256'], 'asset_sha256')
            if runtime:
                meta = f'{model_name}/.cache/huggingface/download/{filename}.metadata'
                allowed.add(meta)
                meta_path = root / meta
                no_symlink_parents(meta_path)
                check(meta_path.is_file() and meta_path.stat().st_size < 4096
                      and meta_path.read_text() == metadata(model, entry), 'asset_runtime_metadata')
    if exact:
        check_tree(root, allowed)


def metadata(model, entry):
    return model['revision'] + '\n' + entry['sha256'] + '\n0\n'


def remove_tree(path):
    # Never traverse a symlink; ancestors must already belong to RUNNER_TEMP.
    no_symlink_parents(path, leaf=False)
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def download(root, manifest):
    from huggingface_hub import snapshot_download
    for name, model in manifest['models'].items():
        snapshot_download(model['repository'], revision=model['revision'],
                          local_dir=root / name, allow_patterns=list(model['files']), max_workers=4)


def prepare(cache, runtime, manifest, downloader=download):
    pins.require_keyless()
    cache, runtime = Path(cache), Path(runtime)
    no_symlink_parents(cache, leaf=False)
    no_symlink_parents(runtime, leaf=False)
    check(cache != runtime and cache not in runtime.parents and runtime not in cache.parents,
          'asset_paths_overlap')
    fresh = False
    try:
        verify(cache, manifest)
    except (CacheError, OSError):
        remove_tree(cache)
        # Exactly one fresh download; no inference, keys or cached HF metadata.
        with tempfile.TemporaryDirectory(prefix='bora-public-download-', dir=cache.parent) as td:
            staging = Path(td)
            downloader(staging, manifest)
            verify(staging, manifest, exact=False)
            cache.mkdir(mode=0o700)
            for name, model in manifest['models'].items():
                for filename in model['files']:
                    target = cache / name / filename
                    target.parent.mkdir(parents=True, exist_ok=True)
                    (staging / name / filename).replace(target)
        verify(cache, manifest)
        fresh = True
    remove_tree(runtime)
    runtime.mkdir(mode=0o700)
    for name, model in manifest['models'].items():
        for filename, entry in model['files'].items():
            target = runtime / name / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(cache / name / filename, target)
            meta = runtime / name / '.cache/huggingface/download' / (filename + '.metadata')
            meta.parent.mkdir(parents=True, exist_ok=True)
            meta.write_text(metadata(model, entry))
    return fresh


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('prepare', 'verify'))
    args = parser.parse_args()
    pins.require_keyless()
    base = Path(os.environ['RUNNER_TEMP']).absolute()
    no_symlink_parents(base)
    cache, runtime = base / 'bora-public-assets', base / 'bora-verified-models'
    manifest = load_manifest()
    if args.command == 'prepare':
        fresh = prepare(cache, runtime, manifest)
        with open(os.environ['GITHUB_ENV'], 'a') as env:
            env.write('BORA_MODEL=' + str(runtime / 'qwen') + '\n')
            env.write('BORA_ASR=' + str(runtime / 'asr') + '\n')
        print(json.dumps({'status': 'public_assets_prepared', 'fresh_download': fresh}))
    else:
        verify(runtime, manifest, runtime=True)
        print(json.dumps({'status': 'pinned_public_assets_verified'}))


if __name__ == '__main__':
    main()
