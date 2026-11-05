"""Build a pinned Android/offline catalog from a complete GitHub release snapshot.

Does not generate audio or mutate GitHub. Refuses incomplete/ambiguous snapshots.
"""
import argparse, gzip, hashlib, json, pathlib, re
ROOT=pathlib.Path(__file__).resolve().parent
REPO='quddnr0107-lgtm/korean-voice'

def build(snapshot, plan):
    expected={b['id']:(job['id'],len(b['items'])) for job in plan['jobs'] for b in job['bundles']}
    actual={};tags=set(); prefix='sulsul-b1-'+plan['version']+'-'
    for release in snapshot['releases']:
        tag=release['tag_name']
        if not tag.startswith(prefix):continue
        if release['draft'] or tag in tags:raise ValueError('Draft or duplicate release '+tag)
        tags.add(tag);book=tag[len(prefix):]
        for asset in release['assets']:
            name=asset['name']; key=book+':'+str(int(name[:3])) if re.fullmatch(r'\d{3}\.zip',name) else None
            if key not in expected or key in actual:raise ValueError('Unexpected/duplicate asset '+str(key))
            sha=asset.get('digest','')
            if asset.get('state')!='uploaded' or not re.fullmatch(r'sha256:[0-9a-f]{64}',sha) or asset['size']<=0:raise ValueError('Unverified asset '+key)
            url='https://github.com/'+REPO+'/releases/download/'+tag+'/'+name
            if asset['browser_download_url']!=url:raise ValueError('Unexpected asset URL '+key)
            job,units=expected[key]
            actual[key]={'url':url,'bytes':asset['size'],'sha256':sha[7:],'units':units,'job':job}
    if set(actual)!=set(expected):raise ValueError('Missing chapter assets: '+str(sorted(set(expected)-set(actual))))
    return {'schema':1,'version':plan['version'],'recipe':plan['recipe']['id'],'repository':REPO,'sourceCommit':'bce2be53f45c21938e8e959775c462ea869f0d83','observedAt':snapshot['observedAt'],'verification':'GitHub uploaded state, exact release URL, byte size and SHA-256 metadata for all assets; representative archive bytes separately verified.','totals':{'books':len(tags),'bundles':len(actual),'units':sum(v['units'] for v in actual.values()),'archiveBytes':sum(v['bytes'] for v in actual.values())},'bundles':actual}

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('snapshot',type=pathlib.Path);ap.add_argument('--output',type=pathlib.Path,default=ROOT/'release-catalog.json');args=ap.parse_args()
    with gzip.open(ROOT/'corpus.json.gz','rt',encoding='utf-8') as f: plan=json.load(f)
    catalog=build(json.loads(args.snapshot.read_text()),plan);args.output.write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n');print(json.dumps(catalog['totals']))
