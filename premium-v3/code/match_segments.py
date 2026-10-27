"""Exact immutable audio-segment -> narration-text spans. No fuzzy alignment."""
import hashlib,json,math,re
from pathlib import Path
sha=lambda s:hashlib.sha256(s.encode('utf-8')).hexdigest()
def utf16_length(s):return len(s.encode('utf-16-le'))//2

def match_lesson(lesson):
 text=lesson['narration_text'];expected=lesson.get('narration_sha256')
 canonical=re.search(r'AL-\d{3}$',lesson['id'])
 if not canonical:raise ValueError('Invalid lesson identity')
 canonical=canonical[0]
 if expected and expected!=sha(text):raise ValueError('Narration hash mismatch')
 cursor=0;last_end=0.;out=[];gaps=[];ids=set()
 for seq,segment in enumerate(lesson['segments']):
  part=segment['text']
  if not part:raise ValueError('Empty segment')
  if segment.get('index')!=seq:raise ValueError('Noncontiguous segment index')
  # Scan past whitespace only. Do not seek past omitted nonwhitespace words.
  start=cursor
  while start<len(text) and text[start].isspace():start+=1
  if not text.startswith(part,start):raise ValueError(f"Exact segment mismatch: {lesson['id']} #{seq}")
  end=start+len(part)
  if text[cursor:start]:gaps.append({'char_start_cp':cursor,'char_end_cp':start,'kind':'whitespace'})
  st,en=segment['start_s'],segment['end_s']
  if not all(isinstance(n,(int,float)) and math.isfinite(n) for n in (st,en)) or st<last_end-1e-6 or en<=st:raise ValueError('Invalid segment time order')
  if en>lesson['duration_s']+.06:raise ValueError('Segment exceeds audio duration')
  h=sha(part);sid=f"{lesson['track']}-{canonical}-s{seq+1:04d}-{h[:12]}"
  if sid in ids:raise ValueError('Duplicate segment id')
  ids.add(sid)
  out.append({'id':sid,'index':seq,'text_sha256':h,'char_start_cp':start,'char_end_cp':end,'char_start_utf16':utf16_length(text[:start]),'char_end_utf16':utf16_length(text[:end]),'start_s':st,'end_s':en})
  cursor=end;last_end=en
 if any(not c.isspace() for c in text[cursor:]):raise ValueError('Uncovered narration tail')
 if text[cursor:]:gaps.append({'char_start_cp':cursor,'char_end_cp':len(text),'kind':'whitespace'})
 # Explicit full nonwhitespace coverage and exact substring checks.
 coverage=[False]*len(text)
 for s in out:
  p=text[s['char_start_cp']:s['char_end_cp']]
  if sha(p)!=s['text_sha256']:raise ValueError('Segment slice hash')
  for j in range(s['char_start_cp'],s['char_end_cp']):
   if coverage[j]:raise ValueError('Overlapping spans')
   coverage[j]=True
 if any(not c.isspace() and not coverage[i] for i,c in enumerate(text)):raise ValueError('Nonwhitespace coverage failed')
 return {'id':canonical,'source_id':lesson['id'],'timeline_provenance':'measured_chunk_boundaries','number':lesson.get('number'),'narration_sha256':sha(text),'audio_sha256':lesson.get('audio_sha256'),'duration_s':lesson['duration_s'],'offset_unit':'unicode-codepoint-and-utf16-code-unit','interval':'half-open','narration_length_cp':len(text),'narration_length_utf16':utf16_length(text),'segments':out,'whitespace_gaps':gaps,'validation':{'exact_text_matches':True,'nonwhitespace_coverage_complete':True,'overlap_count':0,'segment_count':len(out),'text_included':False}}

def main():
 source=Path('/workspace/shared/admin-law-voice-full/published/manifest.json');raw=source.read_bytes();m=json.loads(raw)
 assert len(m['lessons'])==10 and [l['number'] for l in m['lessons']]==list(range(1,11))
 assert all(l['access']=='free' for l in m['lessons'])
 result={'version':'admin-law-segment-offsets/v1','namespace':'admin-law/v2/free','source_manifest_sha256':hashlib.sha256(raw).hexdigest(),'timing_kind':'measured-synthesis-chunk-duration','word_alignment':False,'narration_text_included':False,'lessons':[match_lesson(l) for l in m['lessons']]}
 dest=Path(__file__).parent/'free-segment-offsets.json';dest.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
 assert source.read_bytes()==raw
 report={'passed':True,'manifest_unchanged':True,'audio_files_modified':False,'lessons':len(result['lessons']),'segments':sum(len(l['segments']) for l in result['lessons']),'exact_text_matches':True,'nonwhitespace_coverage_complete':True,'premium_content_included':False,'sidecar_sha256':hashlib.sha256(dest.read_bytes()).hexdigest(),'source_manifest_sha256':result['source_manifest_sha256']}
 (Path(__file__).parent/'validation.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
if __name__=='__main__':main()
