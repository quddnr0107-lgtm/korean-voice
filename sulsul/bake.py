#!/usr/bin/env python3
"""Frozen female-B recipe. Resume by chapter; publish only complete, verified ZIPs."""
import os
os.environ['ORT_DISABLE_TELEMETRY']='1'
os.environ['HF_HUB_OFFLINE']='1'
os.environ['TRANSFORMERS_OFFLINE']='1'
os.environ.setdefault('OMP_NUM_THREADS','2')
os.environ.setdefault('OPENBLAS_NUM_THREADS','2')
import argparse, gzip, hashlib, importlib.util, io, json, pathlib, subprocess, sys, time, zipfile
HERE=pathlib.Path(__file__).resolve().parent
REPO='quddnr0107-lgtm/korean-voice'
def plan():
    with gzip.open(HERE/'corpus.json.gz','rt',encoding='utf-8') as f:return json.load(f)
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def gh(*args,check=True):return subprocess.run(['gh',*args],capture_output=True,text=True,check=check)
def release_tag(version,bundle):return 'sulsul-b1-'+version+'-'+bundle.split(':')[0]
def asset_name(bundle):return bundle.split(':')[1].zfill(3)+'.zip'
def released(tag,name):
    r=gh('release','view',tag,'--repo',REPO,'--json','assets',check=False)
    return r.returncode==0 and any(x['name']==name for x in json.loads(r.stdout)['assets'])
def publish(tag,archive,version):
    if gh('release','view',tag,'--repo',REPO,check=False).returncode:
        r=gh('release','create',tag,'--repo',REPO,'--target',os.environ['GITHUB_SHA'],'--prerelease','--title','쉬운술술성경 여성 B · '+tag.rsplit('-',1)[1],'--notes','Voice assets '+version+'. Frozen approved female-B recipe. This is an audio asset release.',check=False)
        if r.returncode and gh('release','view',tag,'--repo',REPO,check=False).returncode:raise RuntimeError('Release creation failed')
    gh('release','upload',tag,str(archive),'--repo',REPO)
def load_engine(assets,engine):
    import numpy as np
    sys.path.insert(0,str(HERE/'vendor'))
    import helper
    spec=importlib.util.spec_from_file_location('sulsul_synth',engine)
    synth=importlib.util.module_from_spec(spec);spec.loader.exec_module(synth)
    tts=helper.load_text_to_speech(str(assets/'onnx'),False)
    style,_=synth.load_blend(helper,str(assets/'voice_styles'),'F4:0.6,F2:0.4')
    return np,synth,tts,style

def render(item,state):
    np,synth,tts,style=state;sr=tts.sample_rate
    np.random.seed(int(item['key'][:8],16))
    units=synth.units_from_plan(synth.plan(item['text'],'calm',None,1))
    if not units:raise ValueError('Empty speech unit')
    units[-1]['hard']=True;units[-1]['pause_ms']=max(units[-1]['pause_ms'],600)
    pieces=[]
    for u in units:
        speed=1.06*u['rate']*(.95 if u['emph'] else 1)
        wav,_=tts._infer([u['text']],['ko'],style,16,float(np.clip(speed,.7,1.6)))
        w=synth.fade(synth.trim(np.asarray(wav,dtype=np.float32).reshape(-1),sr),sr)
        if not np.isfinite(w).all() or len(w)<sr*.08:raise ValueError('Invalid waveform')
        pieces.extend([w,np.zeros(int(sr*u['pause_ms']/1000),dtype=np.float32)])
    out=np.concatenate(pieces);peak=np.abs(out).max()
    if not np.isfinite(peak) or peak<1e-5:raise ValueError('Silent waveform')
    return (out/peak*10**(-2/20)).astype(np.float32),sr

def bake(args,data):
    import numpy as np, soundfile as sf, imageio_ffmpeg
    ff=imageio_ffmpeg.get_ffmpeg_exe(); state=None
    job=next(j for j in data['jobs'] if j['id']==args.job)
    out=args.output/data['version'];out.mkdir(parents=True,exist_ok=True)
    started=time.time();count=0
    for bundle in job['bundles']:
        if args.bundle and bundle['id'] != args.bundle: continue
        tag=release_tag(data['version'],bundle['id']);name=asset_name(bundle['id'])
        if args.publish and released(tag,name):
            print('Published; skip',bundle['id'],flush=True);continue
        folder=out/bundle['id'].replace(':','-');folder.mkdir(exist_ok=True)
        records={}
        for item in bundle['items']:
            target=folder/(item['key']+'.mp3');side=folder/(item['key']+'.json')
            record=None
            if target.is_file() and side.is_file():
                candidate=json.loads(side.read_text())
                if candidate.get('sha256')==digest(target) and candidate.get('version')==data['version']:record=candidate
            if record is None:
                if state is None:state=load_engine(args.assets,args.engine)
                wav,sr=render(item,state)
                buf=io.BytesIO();sf.write(buf,wav,sr,format='WAV',subtype='PCM_16')
                tmp=target.with_suffix('.tmp.mp3')
                subprocess.run([ff,'-v','error','-y','-i','pipe:0','-codec:a','libmp3lame','-q:a','2',str(tmp)],input=buf.getvalue(),check=True)
                subprocess.run([ff,'-v','error','-i',str(tmp),'-f','null','-'],check=True)
                tmp.replace(target)
                record={'version':data['version'],'sha256':digest(target),'bytes':target.stat().st_size,'seconds':round(len(wav)/sr,4)}
                side.write_text(json.dumps(record))
            records[item['key']]=record;count+=1
            print(bundle['id'],len(records),'/',len(bundle['items']),item['key'][:10],flush=True)
        manifest={'schema':1,'version':data['version'],'recipe':data['recipe']['id'],'bundle':bundle['id'],'files':records}
        archive=out/(bundle['id'].replace(':','-')+'.zip')
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_STORED) as z:
            z.writestr('manifest.json',json.dumps(manifest,separators=(',',':')))
            for key in records:z.write(folder/(key+'.mp3'),key+'.mp3')
        with zipfile.ZipFile(archive) as z:
            if z.testzip() is not None:raise ValueError('ZIP failed validation')
        if args.publish:
            upload=out/name;archive.replace(upload);publish(tag,upload,data['version']);upload.unlink()
        print('Complete',bundle['id'],len(records),flush=True)
    print('Rendered',count,'units in',round(time.time()-started),'seconds',flush=True)
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--matrix',action='store_true');ap.add_argument('--job',default='000')
    ap.add_argument('--assets',type=pathlib.Path,default=pathlib.Path('supertonic3'));ap.add_argument('--engine',type=pathlib.Path,default=HERE.parent/'tools/synth-supertonic.py')
    ap.add_argument('--output',type=pathlib.Path,default=pathlib.Path('sulsul-output'));ap.add_argument('--publish',action='store_true');ap.add_argument('--bundle');a=ap.parse_args();data=plan()
    if a.matrix:print(json.dumps({'job':[j['id'] for j in data['jobs']]}))
    else:bake(a,data)
