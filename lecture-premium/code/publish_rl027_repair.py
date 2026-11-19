"""Promote only the verified RL-027 ciphertext from the pinned, protected producer."""
import argparse
import io
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import zipfile

import delivery as d

REPO = 'quddnr0107-lgtm/korean-voice'
BRANCH = 'integrated-lectures-premium-20261028'
TAG = 'bora-fp32-rl027-art15p3-v2-20261119'
LESSON = 'RL-027'
BASE = 'repos/' + REPO
REQUEST = Path(__file__).resolve().parents[1] / 'rl027-release-request.json'
MAX_ZIP = 128 * 1024 * 1024


def require(ok, code):
    if not ok:
        raise ValueError(code)


def request():
    cfg = d.loads(REQUEST.read_bytes())
    require(set(cfg) == {'run_id', 'attempt', 'producer_sha', 'artifact_id', 'artifact_sha256',
                         'descriptor_sha256', 'index_sha256'}, 'request fields')
    require(type(cfg['run_id']) is int and cfg['run_id'] > 0 and type(cfg['attempt']) is int
            and cfg['attempt'] > 0 and type(cfg['artifact_id']) is int and cfg['artifact_id'] > 0, 'request run')
    require(all(isinstance(cfg[k], str) and re.fullmatch('[a-f0-9]{64}', cfg[k])
                for k in ('artifact_sha256', 'descriptor_sha256', 'index_sha256'))
            and re.fullmatch('[a-f0-9]{40}', cfg['producer_sha']), 'request hash')
    return cfg


def unpack_checked(raw, cfg, folder):
    require(len(raw) <= MAX_ZIP and d.sha(raw) == cfg['artifact_sha256'], 'artifact content')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        require(len(entries) == 4 and len({i.filename for i in entries}) == 4, 'artifact count')
        for item in entries:
            name = item.filename
            require(name in {'index.json', 'RL-027.descriptor.json'} or
                    re.fullmatch(r'RL-027\.(?:audio|content)\.[a-f0-9]{32}\.bin', name), 'file scope')
            require(0 < item.file_size <= min(d.cap_for(name), MAX_ZIP)
                    and stat.S_IFMT(item.external_attr >> 16) in (0, stat.S_IFREG), 'file type or length')
            (folder / name).write_bytes(archive.read(item))
    descriptor = d.checked_complete(folder, LESSON)
    require({p.name for p in folder.iterdir()} == d.names(LESSON, descriptor) | {'index.json'},
            'complete file set')
    descriptor_digest = d.sha((folder / 'RL-027.descriptor.json').read_bytes())
    index = (folder / 'index.json').read_bytes()
    expected = dict(version='integrated-lecture-index/v2', release_tag=TAG, count=1, lessons=[
        dict(id=LESSON, sha256=descriptor_digest,
             descriptor_url=f'https://github.com/{REPO}/releases/download/{TAG}/RL-027.descriptor.json')])
    require(descriptor_digest == cfg['descriptor_sha256'] and d.sha(index) == cfg['index_sha256']
            and d.loads(index) == expected, 'verified lecture manifest')
    return {p.name: (p.stat().st_size, 'sha256:' + d.sha(p.read_bytes())) for p in folder.iterdir()}


def api(path):
    return d.gh('api', BASE + '/' + path, json_output=True)


def checked_source(cfg, folder):
    run = api(f"actions/runs/{cfg['run_id']}")
    require(run.get('conclusion') == 'success' and run.get('status') == 'completed'
            and run['head_branch'] == BRANCH and run['head_sha'] == cfg['producer_sha']
            and run['event'] == 'push' and run['run_attempt'] == cfg['attempt']
            and run['path'] == '.github/workflows/rl027-audio-candidate.yml'
            and run['repository']['full_name'] == REPO, 'producer identity')
    jobs = api(f"actions/runs/{cfg['run_id']}/attempts/{cfg['attempt']}/jobs")['jobs']
    require({j['name']: j['conclusion'] for j in jobs} == {'candidate': 'success', 'preflight': 'success'},
            'producer quality jobs')
    artifact = api(f"actions/artifacts/{cfg['artifact_id']}")
    require(artifact['name'] == f"rl027-verified-pack-{cfg['run_id']}-{cfg['attempt']}"
            and artifact['workflow_run']['id'] == cfg['run_id'] and not artifact['expired']
            and artifact['digest'] == 'sha256:' + cfg['artifact_sha256']
            and 0 < artifact['size_in_bytes'] <= MAX_ZIP, 'artifact identity')
    raw = d.gh('api', f"{BASE}/actions/artifacts/{cfg['artifact_id']}/zip", bytes_output=True, timeout=180)
    return unpack_checked(raw, cfg, folder)


def checked_assets(release_id, expected):
    assets = api(f'releases/{release_id}/assets?per_page=100')
    found = set()
    for item in assets:
        name = item['name']
        require(name in expected and name not in found and item['state'] == 'uploaded'
                and (item['size'], item['digest']) == expected[name], 'existing asset mismatch')
        found.add(name)
    return found


def publish(cfg, folder, expected):
    pages = d.gh('api', BASE + '/releases?per_page=100', '--paginate', '--slurp', json_output=True)
    matches = [r for page in pages for r in page if r['tag_name'] == TAG]
    require(len(matches) <= 1, 'duplicate release')
    refs = [r for r in api('git/matching-refs/tags/' + TAG) if r['ref'] == 'refs/tags/' + TAG]
    require(all(r['object']['type'] == 'commit' and r['object']['sha'] == cfg['producer_sha']
                for r in refs) and (matches or not refs), 'existing tag scope')
    if matches:
        release = matches[0]
    else:
        release = d.gh('api', '--method', 'POST', BASE + '/releases',
            '-f', 'tag_name=' + TAG, '-f', 'target_commitish=' + cfg['producer_sha'],
            '-f', 'name=예비군법 제15조 제3항 낭독 교체',
            '-f', 'body=AI 합성 음성. 두 낭독 구간을 교정한 암호화 강의 음원입니다.',
            '-F', 'draft=true', '-f', 'make_latest=false', json_output=True)
    require(release['tag_name'] == TAG and release['target_commitish'] == cfg['producer_sha'],
            'release target')
    release_id = release['id']
    found = checked_assets(release_id, expected)
    missing = sorted(set(expected) - found)
    if missing:
        require(release['draft'] is True, 'incomplete published release')
        d.gh('release', 'upload', TAG, *(str(folder / name) for name in missing), '--repo', REPO, timeout=300)
    require(checked_assets(release_id, expected) == set(expected), 'uploaded asset coverage')
    if release['draft']:
        d.gh('api', '--method', 'PATCH', f'{BASE}/releases/{release_id}',
             '-F', 'draft=false', '-f', 'make_latest=false')
    live = api('releases/tags/' + TAG)
    require(live['id'] == release_id and live['draft'] is False
            and api('git/ref/tags/' + TAG)['object']['sha'] == cfg['producer_sha']
            and checked_assets(release_id, expected) == set(expected), 'published verification')
    print(json.dumps(dict(release_id=release_id, tag=TAG, files=sorted(expected),
                          descriptor_sha256=cfg['descriptor_sha256'], index_sha256=cfg['index_sha256'])))


if __name__ == '__main__':
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('verify', 'publish'))
    mode = parser.parse_args().mode
    cfg = request()
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        files = checked_source(cfg, folder)
        if mode == 'publish':
            require(os.environ.get('GITHUB_REPOSITORY') == REPO
                    and os.environ.get('GITHUB_EVENT_NAME') == 'push'
                    and os.environ.get('GITHUB_REF') == 'refs/heads/' + BRANCH, 'publisher context')
            publish(cfg, folder, files)
