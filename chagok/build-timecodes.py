#!/usr/bin/env python3
"""Generate Chagok 389-line timecodes from actual silence boundaries of final MP3."""
import argparse, json, re, subprocess
from pathlib import Path

def duration(path):
    p=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(path)],check=True,capture_output=True,text=True)
    return float(json.loads(p.stdout)['format']['duration'])

def pauses(path, expected):
    p=subprocess.run(['ffmpeg','-nostdin','-hide_banner','-nostats','-i',str(path),'-af','silencedetect=n=-40dB:d=0.60','-f','null','-'],capture_output=True,text=True)
    if p.returncode: raise RuntimeError(p.stderr[-1200:])
    starts=[float(x) for x in re.findall(r'silence_start:\s*([0-9.]+)',p.stderr)]
    ends=[float(x) for x in re.findall(r'silence_end:\s*([0-9.]+)',p.stderr)]
    if len(starts)!=expected or len(ends)!=expected:
        raise ValueError(f'Expected {expected} silences, got {len(starts)} and {len(ends)} for {path}')
    if not all(0<=a<b for a,b in zip(starts,ends)): raise ValueError('Non-positive pause')
    if not all(ends[i] < starts[i+1] for i in range(expected-1)): raise ValueError('Overlapping pause')
    return ends

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--learn',type=Path,required=True)
    parser.add_argument('--listen',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    a=parser.parse_args()
    le=pauses(a.learn,389*3)
    ee=pauses(a.listen,389)
    ld=duration(a.learn)
    ed=duration(a.listen)
    cues=[]
    for i in range(389):
        cues.append([i+1,round(le[3*i-1] if i else 0,3),round(le[3*i],3),round(le[3*i+1],3),round(le[3*i+2],3),round(ee[i-1] if i else 0,3),round(ee[i],3)])
    assert len(cues)==389
    assert all(c[1]<c[2]<c[3]<c[4] and c[5]<c[6] for c in cues)
    assert all(cues[i][4]==cues[i+1][1] and cues[i][6]==cues[i+1][5] for i in range(388))
    assert abs(cues[-1][4]-ld)<0.2 and abs(cues[-1][6]-ed)<0.2
    out={'version':1,'count':389,'unit':'seconds','durations':{'learn':ld,'listen':ed},'format':'[number, learn_start, korean_start, english_again_start, learn_end, listen_start, listen_end]','cues':cues}
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(out,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    print('verified',len(cues),'learn_seconds',ld,'listen_seconds',ed,'file_bytes',a.out.stat().st_size)
if __name__=='__main__': main()
