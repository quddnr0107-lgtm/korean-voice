"""Prepare independently playable 48 kbps parts from authenticated studio masters."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'lecture-premium' / 'code'))
import lecture_crypto as crypto
import lecture_pack as pack
import transport as tr
from namespace_guard import validate_snapshot
from mp3_gapless48 import repair

COURSES = {'TB': 25, 'RL': 34, 'DR': 78, 'MS': 166}
REPO = 'quddnr0107-lgtm/korean-voice'
SETTINGS = 'verified-bora-48k-v1-20261128'
EXTRA = {'MS-018', 'MS-022', 'MS-053'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def enc(value):
    return json.dumps(value, ensure_ascii=True, separators=(',', ':'), sort_keys=True).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def tag(course):
    return 'bora-48k-' + course.lower() + '-v1-20261128'


def paths(group):
    pins = json.loads((HERE / 'playback48-sources.json').read_text())
    ids = sorted(set(pins) - EXTRA)[group::20] if group < 20 else sorted(EXTRA)
    return [(ident, pins[ident]) for ident in ids]


def inventory(gh, release):
    return {v['name']: v for v in gh.assets(release) if v['state'] == 'uploaded'}


def fetch(group, folder):
    gh = tr.GitHubBackend()
    source = {}
    previous = {}
    for ident, pin in paths(group):
        course = ident[:2]
        if course not in previous:
            release = tr._release(gh, tag(course))
            previous[course] = inventory(gh, release) if release else {}
        if ident + '.descriptor.json' in previous[course]:
            with tempfile.TemporaryDirectory() as tmp:
                check = Path(tmp, ident + '.descriptor.json')
                tr._fetch_final_object(gh, previous[course][check.name], check, tr.DESCRIPTOR_CAP)
                desc, objects = tr._read_descriptor(check, ident)
                require(all(name in previous[course] for name in objects.values()), 'incomplete existing lesson')
            continue
        key = pin['release_id']
        if key not in source:
            source[key] = inventory(gh, {'id': key})
        assets = source[key]
        out = folder / ident
        out.mkdir(parents=True)
        name = ident + '.descriptor.json'
        asset = assets[name]
        require(asset['id'] == pin['asset_id'] and asset['digest'] == 'sha256:' + pin['sha'], 'source descriptor pin')
        tr._fetch_final_object(gh, asset, out / name, tr.DESCRIPTOR_CAP)
        _, objects = tr._read_descriptor(out / name, ident)
        for k, obj in objects.items():
            tr._fetch_final_object(gh, assets[obj], out / obj, tr.FINAL_OBJECT_CAPS[k])


def pcm(path):
    return subprocess.check_output(['ffmpeg','-nostdin','-v','error','-xerror','-i',str(path),
        '-map','0:a:0','-c:a','pcm_s16le','-f','s16le','-'])


def transcode(raw, expected, work):
    src, dest = work / 'source.mp3', work / 'compressed.mp3'
    src.write_bytes(raw)
    decoded = pcm(src)
    require(abs(len(decoded)//2 - expected) <= 1, 'source part duration')
    # The original timing recipe allows a one-sample codec delay tolerance.
    audio = decoded[:expected*2].ljust(expected*2, b'\0')
    subprocess.run(['ffmpeg','-nostdin','-v','error','-xerror','-y','-f','s16le','-ar','44100',
        '-ac','1','-i','pipe:0','-map_metadata','-1','-c:a','libmp3lame','-b:a','48k',
        '-write_xing','1',str(dest)], input=audio, check=True)
    count = len(pcm(dest))//2
    if 1 < count - expected <= 46:
        dest.write_bytes(repair(dest.read_bytes(), expected, count))
        count = len(pcm(dest))//2
    require(abs(count-expected) <= 1, 'compressed part duration')
    info = subprocess.check_output(['ffprobe','-v','error','-select_streams','a:0','-show_entries',
        'stream=codec_name,channels,bit_rate,sample_rate','-of','json',str(dest)])
    stream = json.loads(info)['streams'][0]
    require((stream['codec_name'],stream['channels'],stream['bit_rate'],stream['sample_rate']) ==
            ('mp3', 1, '48000', '44100'), 'playback format')
    return dest.read_bytes()


def transform(folder, target):
    import socket
    validate_snapshot(os.getuid(),os.getuid(),socket.if_nameindex(),Path('/proc/net/route').read_text(),
        os.readlink('/proc/self/ns/net'),os.environ['HOST_NS'],Path('/proc/net/ipv6_route').read_text())
    key = base64.b64decode(os.environ.pop('MEDIA_KEY'), validate=True)
    require(len(key)==32, 'key size')
    target.mkdir()
    for item in sorted(folder.iterdir()):
        ident = item.name
        desc, names = tr._read_descriptor(item/(ident+'.descriptor.json'), ident)
        old = desc['audio']
        blob = (item/names['audio']).read_bytes()
        pack.validate_pack(old, blob)
        raw, _ = crypto.open_range(key, desc['content'], (item/names['content']).read_bytes(),
            expected_id=ident,expected_kind='private-metadata',expected_key_id=tr.KEY_ID)
        rec = json.loads(raw)
        require(rec['id']==ident and rec['audio_pack_sha256']==old['pack_sha256'] and
                rec['total_samples']==old['total_samples'] and rec['segments'] and
                rec['segments'][-1]['end_sample']==old['total_samples'], 'source timing identity')
        out = target / ident
        out.mkdir()
        pid, headers, sealed = os.urandom(16).hex(), [], bytearray()
        with tempfile.TemporaryDirectory() as tmp:
            for h in old['parts']:
                i, offset = h['part_index'], h['pack_offset']
                raw = pack.open_part(key,h,blob[offset:offset+h['cipher_bytes']],expected_id=ident,
                    expected_part_index=i,expected_pack_id=old['object_id'])
                lo, hi = h['start_sample'], h['end_sample']
                chunk = transcode(raw, hi-lo, Path(tmp))
                header, ct = pack.seal_part(key,chunk,ident,pid,i,lo,hi,len(sealed))
                require(pack.open_part(key,header,ct,expected_id=ident,expected_part_index=i,
                    expected_pack_id=pid)==chunk,'roundtrip')
                sealed.extend(ct)
                headers.append(header)
        audio = bytes(sealed)
        new = pack.make_pack_manifest(ident,pid,headers,audio)
        require(new['total_samples']==old['total_samples'], 'final duration')
        # Publish only player metadata inside the encrypted content envelope.
        meta = dict(rec)
        meta.update(audio_pack_sha256=new['pack_sha256'], total_samples=new['total_samples'],
                    sample_rate=new['sample_rate'], playback_encoding=SETTINGS)
        m, sealed_meta = crypto.seal(key,enc(meta),ident,'private-metadata',tr.KEY_ID)
        plain,_ = crypto.open_range(key,m,sealed_meta,expected_id=ident,
            expected_kind='private-metadata',expected_key_id=tr.KEY_ID)
        require(plain==enc(meta),'metadata roundtrip')
        (out/f'{ident}.content.{m["object_id"]}.bin').write_bytes(sealed_meta)
        (out/f'{ident}.audio.{pid}.bin').write_bytes(audio)
        (out/(ident+'.descriptor.json')).write_bytes(enc(dict(version=desc['version'],
            lesson_id=ident,audio=new,content=m)))
        print(ident, 'verified', len(headers), 'parts', flush=True)


def upload(group, folder):
    gh=tr.GitHubBackend()
    for out in sorted(folder.iterdir()):
        ident=out.name
        require(ident in dict(paths(group)), 'upload scope')
        tr.validate_final_directory(out,ident[:2],partial=True,profile='single_pass')
        files = list(out.glob('*.bin'))
        require(len(files)==2 and len(list(out.iterdir()))==3,'encrypted files only')
        release = tr._release(gh,tag(ident[:2]))
        for f in sorted(files): gh.upload(release,f)
        gh.upload(release,out/(ident+'.descriptor.json'))


def finish():
    gh=tr.GitHubBackend()
    verified = {}
    for course,n in COURSES.items():
        rel=tr._release(gh,tag(course))
        assets=inventory(gh,rel)
        lessons=[]
        for i in range(1,n+1):
            ident=f'{course}-{i:03d}'
            name=ident+'.descriptor.json'
            if name not in assets:
                break
            require(any(k.startswith(ident+'.audio.') for k in assets) and
                    any(k.startswith(ident+'.content.') for k in assets),'missing encrypted assets')
            lessons.append(dict(id=ident,sha256=assets[name]['digest'].removeprefix('sha256:'),
                descriptor_url=f'https://github.com/{REPO}/releases/download/{tag(course)}/{name}'))
        if len(lessons)!=n:
            print(course,'awaiting remaining lessons',flush=True)
            continue
        raw=enc(dict(version='integrated-lecture-index/v2',release_tag=tag(course),count=n,lessons=lessons))
        if 'index.json' in assets:
            require(assets['index.json']['digest']=='sha256:'+digest(raw),'existing index differs')
        else:
            with tempfile.TemporaryDirectory() as d:
                p=Path(d,'index.json');p.write_bytes(raw);gh.upload(rel,p)
        durations = {}
        with tempfile.TemporaryDirectory() as tmp:
            for row in lessons:
                ident = row['id']
                path = Path(tmp, ident+'.descriptor.json')
                tr._fetch_final_object(gh,assets[path.name],path,tr.DESCRIPTOR_CAP)
                desc, objects = tr._read_descriptor(path,ident)
                require(all(k in assets for k in objects.values()),'descriptor references missing object')
                require(assets[objects['audio']]['digest']=='sha256:'+desc['audio']['pack_sha256'],'pack digest')
                durations[ident] = desc['audio']['total_samples']
        if rel['draft']:
            gh.publish(rel)
        verified[course] = dict(index_sha256=digest(raw),tag=tag(course),samples=durations)
        print(course,digest(raw),n,flush=True)
    path='bora-production/playback48-verified.json'
    ref='integrated-lectures-premium-20261028'
    endpoint=f'/repos/{REPO}/contents/{path}'
    existing=gh._run(['api',endpoint+'?ref='+ref],missing=True)
    body=dict(message='Record verified 48 kbps playback timing and release pins',branch=ref,
              content=base64.b64encode(enc(verified)).decode())
    if existing is not None:
        obj=json.loads(existing)
        if base64.b64decode(obj['content'])==enc(verified): return
        body['sha']=obj['sha']
    gh._run(['api','--method','PUT',endpoint,'--input','-'],data=enc(body))


if __name__ == '__main__':
    a=sys.argv[1:]
    if a[0]=='prepare':
        gh=tr.GitHubBackend()
        for c in COURSES: tr._release(gh,tag(c),create=True)
    elif a[0]=='finish': finish()
    elif a[0]=='fetch': fetch(int(a[1]),Path(a[2]))
    elif a[0]=='seal': transform(Path(a[1]),Path(a[2]))
    elif a[0]=='upload': upload(int(a[1]),Path(a[2]))
    else: raise ValueError('unknown operation')
