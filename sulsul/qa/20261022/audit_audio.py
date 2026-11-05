#!/usr/bin/env python3
"""Inspect already generated MP3 bytes. Does not synthesize or rate listening quality.

Requires ffmpeg, ffprobe, Node.js and NumPy. Pass --android with the canonical
Android source directory to derive the application's real text/mode order.
"""
import argparse, concurrent.futures, gzip, hashlib, json, math, pathlib, re
import subprocess, urllib.request, zipfile, datetime
import numpy as np

TARGETS = ['GEN:1', 'PSA:23', 'PSA:119', 'MAT:1', 'JHN:1']
def sha(raw): return hashlib.sha256(raw).hexdigest()
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--android',required=True);ap.add_argument('--cache',required=True);args=ap.parse_args()
    here=pathlib.Path(__file__).resolve().parent; repo=here.parents[2]
    cat=json.loads((repo/'sulsul/release-catalog.json').read_text())
    corpus=json.loads(gzip.decompress((repo/'sulsul/corpus.json.gz').read_bytes()))
    bundles={b['id']:b for j in corpus['jobs'] for b in j['bundles']}
    cache=pathlib.Path(args.cache).resolve();cache.mkdir(parents=True,exist_ok=True)
    app=pathlib.Path(args.android).resolve()
    node=r'''
const fs=require('fs'),p=process.argv[1],crypto=require('crypto');
const C=require(p+'/web/core.js'),B=require(p+'/web/full-bible.js'),E=require(p+'/web/easy.js');
const read=n=>JSON.parse(fs.readFileSync(p+'/web/data/'+n+'.json','utf8'));
const scenes=E.attach(B.scenes(read('bible'),read('ai-review')),read('easy-reading'));
const targets=new Set(['GEN:1','PSA:23','PSA:119','MAT:1','JHN:1']);
const rows=[];
for(const s of scenes){const chapter=s.bookId+':'+s.chapter;if(!targets.has(chapter))continue;
 for(const mode of ['easy','source'])for(const v of s.verses){if(v.sourceRange&&v.n!==v.sourceRange.start)continue;
 const text=C.speechText(v,mode);rows.push({chapter,mode,verse:v.n,text,key:crypto.createHash('sha256').update(text).digest('hex')});}}
process.stdout.write(JSON.stringify(rows));
'''
    reading=json.loads(subprocess.check_output(['node','-e',node,str(app)],text=True))
    labels={r['key']:r for r in reading}; records=[];chapter_rows=[]
    for key in TARGETS:
        entry=cat['bundles'][key];zpath=cache/(key.replace(':','-')+'.zip')
        if not zpath.is_file() or sha(zpath.read_bytes())!=entry['sha256']:
            req=urllib.request.Request(entry['url'],headers={'User-Agent':'sulsul-byte-audit'})
            with urllib.request.urlopen(req,timeout=60) as response:raw=response.read(entry['bytes']+1)
            assert len(raw)==entry['bytes'] and sha(raw)==entry['sha256'];zpath.write_bytes(raw)
        raw=zpath.read_bytes();assert len(raw)==entry['bytes'] and sha(raw)==entry['sha256']
        expected={x['key']:x for x in bundles[key]['items']}; folder=cache/key.replace(':','-');folder.mkdir(exist_ok=True)
        with zipfile.ZipFile(zpath) as z:
            assert z.testzip() is None
            names=z.namelist();assert len(names)==len(set(names)) and set(names)=={'manifest.json',*(k+'.mp3' for k in expected)}
            m=json.loads(z.read('manifest.json'));assert (m['schema'],m['version'],m['recipe'],m['bundle'])==(1,cat['version'],cat['recipe'],key)
            assert set(m['files'])==set(expected)
            for k,meta in m['files'].items():
                audio=z.read(k+'.mp3');assert len(audio)==meta['bytes'] and sha(audio)==meta['sha256'] and meta['version']==cat['version']
                assert sha(expected[k]['text'].encode())==k and labels[k]['text']==expected[k]['text']
                path=folder/(k+'.mp3');path.write_bytes(audio)
                records.append(dict(labels[k],path=str(path),bytes=len(audio),sha256=meta['sha256'],manifestSeconds=meta['seconds']))
        chapter_rows.append({'chapter':key,'archiveBytes':len(raw),'archiveSha256':sha(raw),'files':len(expected),'archiveVerified':True})

    def inspect(row):
        path=row['path'];probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','a:0','-show_streams','-show_format','-of','json',path],text=True))
        stream=probe['streams'][0];rate=int(stream['sample_rate']);channels=int(stream['channels'])
        decoded=subprocess.run(['ffmpeg','-nostdin','-hide_banner','-nostats','-i',path,'-af','ebur128=peak=true','-f','f32le','-acodec','pcm_f32le','-'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
        pcm=np.frombuffer(decoded.stdout,dtype='<f4');assert len(pcm)>0 and np.isfinite(pcm).all()
        samples=pcm.reshape(-1,channels);mono=np.sqrt(np.mean(samples.astype('float64')**2,axis=1));duration=len(samples)/rate
        frame=max(1,round(rate*.02));rms=np.array([np.sqrt(np.mean(mono[i:i+frame]**2)) for i in range(0,len(mono),frame)])
        active=np.flatnonzero(rms>10**(-45/20));assert len(active)>0
        stderr=decoded.stderr.decode(errors='replace')
        loud=re.findall(r'Integrated loudness:\s*I:\s*([-\d.]+)',stderr)
        peak=re.findall(r'True peak:\s*Peak:\s*([-\d.]+)',stderr)
        return {**row,'codec':stream['codec_name'],'sampleRate':rate,'channels':channels,'decodedSeconds':duration,'containerSeconds':float(probe['format']['duration']),
                'decodePassed':True,'clippedSamples':int(np.count_nonzero(np.abs(pcm)>=1)),'peakAmplitude':float(np.max(np.abs(pcm))),
                'lufs':float(loud[-1]) if loud else None,'truePeakDbfs':float(peak[-1]) if peak else None,
                'leadingSilenceSeconds':float(active[0]*frame/rate),'trailingSilenceSeconds':float(max(0,duration-(active[-1]+1)*frame/rate))}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool: results=list(pool.map(inspect,records))
    bykey={r['key']:r for r in results}
    transitions=[]
    for chapter in chapter_rows:
        for mode in ['easy','source']:
            rows=[bykey[r['key']] for r in reading if r['chapter']==chapter['chapter'] and r['mode']==mode]
            chapter[mode+'Seconds']=sum(r['decodedSeconds'] for r in rows)
            for left,right in zip(rows,rows[1:]):
                transitions.append({'chapter':chapter['chapter'],'mode':mode,'fromVerse':left['verse'],'toVerse':right['verse'],
                  'fileEdgeSilenceSeconds':left['trailingSilenceSeconds']+right['leadingSilenceSeconds'],
                  'loudnessJumpLu':abs(left['lufs']-right['lufs']) if left['lufs'] is not None and right['lufs'] is not None else None})
    lufs=[r['lufs'] for r in results if r['lufs'] is not None];tails=[r['trailingSilenceSeconds'] for r in results]
    summary={'schema':1,'observedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),'version':cat['version'],'recipe':corpus['recipe'],
      'scope':'actual downloaded ZIP/MP3 byte, decode and signal analysis; NOT human listening, ASR or Android device playback',
      'method':{'silenceThresholdDbfs':-45,'rmsFrameMs':20,'loudness':'FFmpeg ebur128 with true-peak; no normalization or synthesis','appOrder':'SulsulBible.scenes + SulsulEasy.attach + SulsulCore.speechText'},
      'totals':{'archives':len(chapter_rows),'archiveBytes':sum(c['archiveBytes'] for c in chapter_rows),'files':len(results),'decodePass':len(results),
        'decodedSeconds':sum(r['decodedSeconds'] for r in results),'clippedSamples':sum(r['clippedSamples'] for r in results),
        'lufsMin':min(lufs),'lufsMedian':float(np.median(lufs)),'lufsMax':max(lufs),'truePeakMaxDbfs':max(r['truePeakDbfs'] for r in results if r['truePeakDbfs'] is not None),
        'trailingSilenceMin':min(tails),'trailingSilenceMedian':float(np.median(tails)),'trailingSilenceMax':max(tails)},
      'chapters':chapter_rows,'largestLoudnessJumps':sorted(transitions,key=lambda r:r['loudnessJumpLu'] or 0,reverse=True)[:20],
      'checks':{'zipPinsAndCRC':True,'exactManifestMembers':True,'allFileDigests':True,'corpusAndAppText':True,'allDecoded':True,'humanListening':False,'devicePlayback':False},
      'files':[{k:v for k,v in r.items() if k!='path'} for r in results]}
    (here/'audio-quality.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    listen=cache/'listening';listen.mkdir(exist_ok=True);lines=[];cues=[];time=0
    for r in reading:
        if r['mode']!='easy':continue
        a=bykey[r['key']];lines.append("file '"+a['path'].replace("'","'\\''")+"'")
        cues.append({**r,'startSecondsApprox':time,'decodedSeconds':a['decodedSeconds']});time+=a['containerSeconds']
    listing=listen/'concat.txt';listing.write_text('\n'.join(lines)+'\n')
    output=listen/'sulsul-female-b-listening-5chapters.mp3'
    subprocess.run(['ffmpeg','-y','-nostdin','-v','error','-f','concat','-safe','0','-i',str(listing),'-c','copy',str(output)],check=True)
    (listen/'sulsul-female-b-listening-cues.json').write_text(json.dumps({'note':'stream-copy concatenation; approximate cue times include source container padding; does not measure app file handoff','cues':cues},ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'totals':summary['totals'],'chapters':chapter_rows,'listeningFile':str(output)},ensure_ascii=False))
if __name__=='__main__': main()
