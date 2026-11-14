#!/usr/bin/env python3
"""Public generated sine only. No credentials, private input, or networking.
Usage: python probe_gapless_ffmpeg611.py /absolute/path/to/ffmpeg
Produces JSON evidence; does not modify production code.
"""
import array, hashlib, json, math, pathlib, subprocess, sys, tempfile
from mp3_gapless import repair
ffmpeg=sys.argv[1]
def run(args, data=None):
 return subprocess.run([ffmpeg,'-nostdin','-v','error',*args],input=data,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True).stdout
def decode(p):return run(['-i',str(p),'-f','f32le','-ac','1','-ar','44100','pipe:1'])
def header(b):
 start=10+sum(b[6+i]<<(7*(3-i)) for i in range(4)) if b[:3]==b'ID3' else 0
 return start,start+21
version=subprocess.check_output([ffmpeg,'-version'],text=True).splitlines()[0]
results=[]
with tempfile.TemporaryDirectory(prefix='public-sine-') as d:
 for n in [11520,11521,11522,11540,11565,11566,11567,44100,44101,1152001]:
  p=pathlib.Path(d)/f'{n}.mp3'; pcm=array.array('f',(0.1*math.sin(2*math.pi*440*i/44100) for i in range(n)))
  if sys.byteorder!='little':pcm.byteswap()
  run(['-y','-f','f32le','-ar','44100','-ac','1','-i','pipe:0','-map_metadata','-1','-c:a','libmp3lame','-b:a','128k','-write_xing','1',str(p)],pcm.tobytes())
  original=p.read_bytes();decoded=len(decode(p))//4;start,x=header(original);encoder=original[x+120:x+129]
  item=dict(expected=n,decoded=decoded,delta=decoded-n,encoder=encoder.decode('ascii'))
  if 1<decoded-n<=46:
   ns={'repair':repair}
   fixed=ns['repair'](original,n,decoded)
   assert len(fixed)==len(original),'byte length changed'
   assert fixed[start+417:]==original[start+417:],'audio frames changed'
   p.write_bytes(fixed);after=len(decode(p))//4
   assert abs(after-n)<=1,'unchanged production sample gate failed'
   item.update(repaired=after,audio_frames_identical=True,byte_size_identical=True,audio_sha256=hashlib.sha256(original[start+417:]).hexdigest())
   for mutant in [original[:-1],original[:x+120]+b'Unknown!!'+original[x+129:]]:
    try:ns['repair'](mutant,n,decoded)
    except ValueError:pass
    else:raise AssertionError('corrupt/unknown input accepted')
   item['negative_tests_pass']=True
  else:assert abs(decoded-n)<=1
  results.append(item)
assert any(item.get("repaired") is not None and item["encoder"] == "Lavc60.31" for item in results), "FFmpeg 6.1.1 padding edge not reproduced"
print(json.dumps(dict(version=version,results=results),indent=2))
