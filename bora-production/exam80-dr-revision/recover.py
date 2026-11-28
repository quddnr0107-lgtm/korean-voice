"""Recover reviewed correction ciphertext from a particular production run; synthesis is excluded."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile


REPO = 'quddnr0107-lgtm/korean-voice'
BRANCH = 'integrated-lectures-premium-20261028'
PINS = {
    'DR-052': (46152122306, '5fd2430ff03fa721e7549925eebf37b830fda510', ''),
    'DR-054': (46152122306, '5fd2430ff03fa721e7549925eebf37b830fda510', ''),
    'DR-038': (46153341588, '663ef791656115d662a9d52d0d203f569f169f98', 'batch2/'),
    'DR-047': (46153341588, '663ef791656115d662a9d52d0d203f569f169f98', 'batch2/'),
}
ROOT = '/repos/' + REPO + '/actions/'
VERIFIED = 'Verify each corrected paragraph offline against private source and decoded samples'
PRIVATE_STORE = "Persist and read back verified files from the owner's private video branch"


def check(ok, msg):
    if not ok:
        raise SystemExit('Recovery stopped: ' + msg)


def api(path, raw=False):
    data = subprocess.run(['gh','api',ROOT + path], check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, timeout=120).stdout
    return data if raw else json.loads(data)


def rows():
    check(os.getenv('GITHUB_REPOSITORY') == REPO and os.getenv('GITHUB_REF') == 'refs/heads/'+BRANCH
          and os.getenv('GITHUB_EVENT_NAME') == 'push', 'workflow scope')
    request = json.loads(Path(__file__).with_name('recover-request.json').read_text())
    check(set(request) == {'schema','artifacts'} and request['schema'] == 'exam80-private-export-recovery/v1',
          'request schema')
    records = request['artifacts']
    check(isinstance(records,dict) and 0 < len(records) <= 4 and set(records) <= PINS.keys(), 'lesson scope')
    for lesson, asset in records.items():
        check(set(asset) == {'attempt','artifact_id','artifact_digest'} and asset['attempt'] == 1
              and type(asset['artifact_id']) is int and asset['artifact_id'] > 0
              and re.fullmatch('[0-9a-f]{64}', asset['artifact_digest']), 'artifact pin')
    return records


def verify(lesson, asset):
    run_id, producer_sha, _ = PINS[lesson]
    run = api('runs/'+str(run_id))
    check(run['repository']['full_name'] == REPO and run['head_branch'] == BRANCH
          and run['head_sha'] == producer_sha and run['run_attempt'] == asset['attempt'], 'producer identity')
    jobs = api(f'runs/{run_id}/attempts/{asset["attempt"]}/jobs?per_page=100')['jobs']
    match = [j for j in jobs if j['name'] == 'Private directive correction ('+lesson+')']
    check(len(match) == 1 and match[0]['conclusion'] == 'failure', 'failed private store job scope')
    steps = {s['name']:s['conclusion'] for s in match[0]['steps']}
    check(steps.get(VERIFIED) == 'success' and steps.get(PRIVATE_STORE) == 'failure',
          'producer verification evidence')
    artifact = api('artifacts/'+str(asset['artifact_id']))
    check(artifact['workflow_run']['id'] == run_id and artifact['workflow_run']['head_sha'] == producer_sha
          and artifact['name'] == f'exam80-correction-{lesson}-{run_id}-{asset["attempt"]}'
          and not artifact['expired'] and 128 < artifact['size_in_bytes'] < 32*1024**2
          and artifact['digest'] == 'sha256:'+asset['artifact_digest'], 'artifact scope')


def download(lesson, asset, directory):
    verify(lesson, asset)
    raw = api('artifacts/'+str(asset['artifact_id'])+'/zip', raw=True)
    check(len(raw)<32*1024**2 and hashlib.sha256(raw).hexdigest()==asset['artifact_digest'], 'zip digest')
    with zipfile.ZipFile(io.BytesIO(raw)) as data:
        info = data.infolist()
        check(len(info)==2 and {v.filename for v in info}=={'manifest.json','payload.bin'}
              and sum(x.file_size for x in info)<32*1024**2 and all(x.file_size>0 for x in info),
              'cipher members')
        manifest = data.read('manifest.json')
        header = json.loads(manifest)
        check(header['lesson_id']==lesson and header['kind']=='checkpoint'
              and header['key_id']=='exam80-dr-table-revision-v1', 'sealed scope')
        directory.mkdir(parents=False, exist_ok=False, mode=0o700)
        (directory/'manifest.json').write_bytes(manifest)
        (directory/'payload.bin').write_bytes(data.read('payload.bin'))


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['plan','fetch'])
    p.add_argument('--lesson',choices=PINS)
    p.add_argument('--out',type=Path)
    args=p.parse_args()
    os.umask(0o077)
    requests=rows()
    if args.action=='plan':
        for lesson, asset in requests.items():
            verify(lesson,asset)
        with open(os.environ['GITHUB_OUTPUT'],'a') as output:
            print('lessons='+json.dumps(sorted(requests)),file=output)
    else:
        check(args.lesson in requests and args.out and args.out.is_absolute(), 'fetch scope')
        download(args.lesson,requests[args.lesson],args.out)
