"""Create a separate immutable encrypted preview release; never touches old media."""
import hashlib
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

from run import REPO, enc, digest, unb, validate_config, validate_request

TAG = 'kim-bora-tb001-preview-20261112'
API = 'https://api.github.com/repos/' + REPO


def main():
    if (os.environ.get('GITHUB_REPOSITORY') != REPO
            or os.environ.get('GITHUB_REF') != 'refs/heads/integrated-lectures-premium-20261028'
            or os.environ.get('ADMIN_LAW_PREMIUM_SOURCE_KEY_B64')):
        raise RuntimeError('publish context')
    token = os.environ['GH_TOKEN']
    cfg = validate_config(json.loads(Path('kim-bora-tb-pilot/config.json').read_text()))
    validate_request(json.loads(Path('kim-bora-tb-pilot/request.json').read_text()), cfg)
    directory = Path('bora-cipher')
    paths = sorted(directory.glob('*.sealed.json'))
    expected = [f'group_{i:03d}.sealed.json' for i in range(len(cfg['groups']))]
    if [p.name for p in paths] != expected:
        raise ValueError('incomplete encrypted group inventory')
    blocked_groups = []
    for i, path in enumerate(paths):
        data = json.loads(path.read_text())
        if set(data) != {'header', 'cipher_sha256', 'cipher'}:
            raise ValueError('unexpected public fields')
        header = data['header']
        if (header.get('schema') != 'kim-bora-tb-pilot-envelope/v1'
                or header.get('lesson_id') != 'TB-001'
                or header.get('group') != i or header.get('unit_range') != cfg['groups'][i]
                or header.get('config_sha256') != digest(enc(cfg))
                or header.get('recipient_public') != cfg['receiver_public']
                or header.get('quality_status') not in ('private_pcm_subset_complete','blocked_requires_review')
                or digest(unb(data['cipher'])) != data['cipher_sha256']):
            raise ValueError('encrypted group identity')
        if header['quality_status'] == 'blocked_requires_review':
            blocked_groups.append(i)

    def request(url, method='GET', data=None, content_type='application/json'):
        if urllib.parse.urlsplit(url).hostname not in ('api.github.com', 'uploads.github.com'):
            raise ValueError('GitHub delivery host')
        body = enc(data) if data is not None and not isinstance(data, bytes) else data
        req = urllib.request.Request(url, data=body, method=method, headers={
            'Authorization': 'Bearer '+token, 'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28', 'Content-Type': content_type,
            'User-Agent': 'kim-bora-tb001-preview'})
        with urllib.request.urlopen(req, timeout=90) as response:
            return json.load(response)

    try:
        request(API+'/releases/tags/'+TAG)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    else:
        raise ValueError('preview tag already exists; refusing overwrite')
    title = '김보라 통합방위법 1강 암호화 '+('진단 결과' if blocked_groups else '검토본')
    body = 'AI 합성 음성 검토용 암호문입니다. 사이트의 기존 강의와 음원은 변경하지 않았습니다. 음질·문장별 동기화 검토 후에만 교체합니다.'
    if blocked_groups:
        body += '\n자동 검증을 통과하지 못한 구간이 '+str(len(blocked_groups))+'개 작업에 포함되어 있습니다. 이 결과는 완성 음원이 아니며, 암호화 진단 자료를 확인하고 해당 구간을 수정해야 합니다.'
    release = request(API+'/releases', 'POST', dict(tag_name=TAG,
        target_commitish=os.environ['GITHUB_SHA'], name=title, body=body,
        draft=True, prerelease=True))
    upload = release['upload_url'].split('{')[0]
    for path in paths:
        request(upload+'?name='+urllib.parse.quote(path.name), 'POST', path.read_bytes(), 'application/octet-stream')
    inventory = request(API+'/releases/'+str(release['id'])+'/assets?per_page=100')
    if sorted(x['name'] for x in inventory) != expected:
        raise ValueError('release inventory mismatch')
    final = request(API+'/releases/'+str(release['id']), 'PATCH', dict(draft=False))
    print(json.dumps(dict(status='encrypted_diagnostics_release_created' if blocked_groups else 'encrypted_preview_release_created',
                          url=final['html_url'], groups=len(paths), blocked_groups=blocked_groups)))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('TB_PREVIEW_DELIVERY_FAILED:'+type(exc).__name__)
        raise SystemExit(1)
