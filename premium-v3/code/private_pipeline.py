"""Premium local draft. Pure input/metadata/envelope helpers; no keys generated here."""
import copy,hashlib,json,re
from crypto_chunks_v3 import seal,open_range
from match_segments import match_lesson,utf16_length
PIPELINE_REVISION='rebuild-r1-track-contiguous-20261027'
RECIPE={'engine':'Supertonic 3','recipe':'k2','recipe_code_sha256':'907f5ec81b4f7302ffbdadedd37a0d6c283b2d85696fa2f59a62cdf44dcd44a8','style':'F2','voice':'female','steps':16,'base_speed':1.05,'r':1.0,'pause_s':0.48,'model_revision':'3cadd1ee6394adea1bd021217a0e650ede09a323','helper_revision':'1e9799e964ea4c0dad7cde993b65c3c813a7b373'}
sha=lambda b:hashlib.sha256(b if isinstance(b,bytes) else b.encode()).hexdigest()
def encoded(x):return json.dumps(x,ensure_ascii=False,separators=(',',':')).encode()
def canonical_id(x):
 m=re.fullmatch(r'AL-0(?:0[1-9]|[1-6][0-9]|7[0-8])$',x)
 if not m:raise ValueError('invalid premium identity')
 return m[0]
def inverse(lesson):
 src=lesson['source_text'];parts=[];bounds={0:0};sp=np=0
 for c in lesson['changes']:
  a,b=c['source_start'],c['source_end']
  if not sp<=a<b<=len(src) or src[a:b]!=c['original']:raise ValueError('invalid source edit')
  v=src[sp:a];parts.append(v)
  for i in range(len(v)+1):bounds[np+i]=sp+i
  np+=len(v);bounds[np]=a;parts.append(c['replacement']);np+=len(c['replacement']);bounds[np]=b;sp=b
 v=src[sp:];parts.append(v)
 for i in range(len(v)+1):bounds[np+i]=sp+i
 if ''.join(parts)!=lesson['narration_text']:raise ValueError('normalization reconstruction')
 return bounds

def clean_md(text):
 out=[];raw=[];i=0
 while i<len(text):
  if text.startswith('**',i):i+=2;continue
  out.append(text[i]);raw.append(i);i+=1
 return ''.join(out),raw

def aligned_paragraphs(data):
 source=data['lesson']['source_text'];out=[];cursor=None
 for p in data['panel']['paragraphs']:
  clean,raw=clean_md(p['text'])
  if cursor is None:
   hits=[m.start() for m in re.finditer(re.escape(clean),source)] if clean else []
   if len(hits)!=1:raise ValueError('first paragraph mapping')
   start=hits[0]
  else:
   start=cursor
   while start<len(source) and source[start].isspace():start+=1
   if source[start:start+len(clean)]!=clean:raise ValueError('ordered paragraph mapping')
  cursor=start+len(clean);out.append((start,cursor,p,raw))
 if cursor is None or source[cursor:].strip():raise ValueError('paragraph tail')
 return out

def validate_input(data):
 if set(data)!={'version','track','namespace','lineage','lesson','panel'} or data['version']!='admin-law-premium-input/v3':raise ValueError('input schema')
 l=data['lesson'];p=data['panel'];ident=canonical_id(l['id'])
 if data['track'] not in ('easy','source') or data['namespace']!='admin-law/v3/'+data['track'] or p.get('track')!=data['track'] or p.get('namespace')!=data['namespace']:raise ValueError('track mismatch')
 if p['id']!=ident or l['number']!=int(ident[-3:]):raise ValueError('identity mismatch')
 for field in ('source','narration'):
  if sha(l[field+'_text'])!=l[field+'_sha256']:raise ValueError('input hash')
 inverse(l)
 if '검증 메모' in l['narration_text']:raise ValueError('excluded notes')
 ids=set()
 for para in p['paragraphs']:
  if para['id'] in ids or not para['id'].startswith(ident+'-'+data['track']+'-p'):raise ValueError('paragraph identity')
  ids.add(para['id'])
  if sha(para['text'])!=para['textSha256']:raise ValueError('paragraph hash')
  clean,_=clean_md(para['text'])
  if not clean:raise ValueError('paragraph empty')
 aligned_paragraphs(data)
 # Educational references are carried, never invented; only JSON arrays accepted.
 for k in ('sources','diagrams','relatedQuestions','relatedMockExams','printQuestions','relatedPractice'):
  if not isinstance(p.get(k,[]),list):raise ValueError('panel collection type')
 return ident

def checkpoint_hash(data):
 return sha(encoded({'input_sha256':sha(encoded(data)),'voice':RECIPE,'metadata_schema':'admin-law-private-metadata/v1','pipeline_revision':PIPELINE_REVISION}))

def source_spans(data,meta):
 l=data['lesson'];bounds=inverse(l);paras=aligned_paragraphs(data)
 offsets=match_lesson(meta)
 for seg,offset in zip(meta['segments'],offsets['segments']):
  seg.update(offset);a,b=offset['char_start_cp'],offset['char_end_cp'];seg['sourceSpans']=[]
  if a not in bounds or b not in bounds:
   seg['source_mapping_status']='unmapped';seg['source_mapping_reason']='boundary_inside_normalization_token';continue
  sa,sb=bounds[a],bounds[b];covered=[False]*(sb-sa);spans=[]
  for pa,pb,p,raw in paras:
   ia,ib=max(sa,pa),min(sb,pb)
   if ia>=ib:continue
   ra,rb=raw[ia-pa],raw[ib-pa-1]+1;fragment=p['text'][ra:rb]
   if clean_md(fragment)[0]!=l['source_text'][ia:ib]:raise ValueError('raw source span')
   start,end=utf16_length(p['text'][:ra]),utf16_length(p['text'][:rb]);original=p['text'].encode('utf-16-le')
   if original[2*start:2*end].decode('utf-16-le')!=fragment:raise ValueError('UTF16 source slice')
   spans.append({'paragraph_id':p['id'],'start_utf16':start,'end_utf16':end,'text':fragment,'text_sha256':sha(fragment),'paragraph_text_sha256':sha(p['text']),'provenance':'verified_normalization_span','source_start_cp':ia,'source_end_cp':ib})
   for i in range(ia-sa,ib-sa):covered[i]=True
  if spans and not any(not x and not l['source_text'][sa+i].isspace() for i,x in enumerate(covered)):
   seg.update(sourceSpans=spans,source_mapping_status='verified')
  elif spans and sa==0 and l.get('heading_text') and l['source_text'][:min(x['source_start_cp'] for x in spans)].strip()==l['heading_text'].strip():seg.update(sourceSpans=spans,source_mapping_status='partial',source_mapping_reason='heading_and_verified_body')
  else:seg.update(source_mapping_status='unmapped',source_mapping_reason='heading_or_nonparagraph_source')
 return meta

def private_metadata(data,audio,segments,metrics):
 ident=validate_input(data);l=data['lesson'];p=data['panel']
 meta={'version':'admin-law-private-metadata/v1','track':data['track'],'namespace':data['namespace'],'lineage':data['lineage'],'id':ident,'number':l['number'],'title':l['title'],'topic_ids':l['topic_ids'],'narration_text':l['narration_text'],'narration_sha256':l['narration_sha256'],'audio_sha256':sha(audio),'checkpoint_hash':checkpoint_hash(data),'voice':RECIPE,'duration_s':metrics['decoded_duration_s'],'segments':segments,'validation':metrics,'listening_verified':False,'timeline_provenance':'measured_chunk_boundaries','paragraphs':p['paragraphs'],'sources':p.get('sources',[]),'diagrams':p.get('diagrams',[]),'relatedQuestions':p.get('relatedQuestions',[]),'relatedMockExams':p.get('relatedMockExams',[]),'panel':p}
 for key in ('printQuestions','printQuestionsStatus','relatedPractice','relatedQuestionsStatus','relatedMockExamsStatus','diagramsStatus','diagramsUnavailableReason','mappingStatus','unmappedParagraphCount','syncMode','sourceDraftSha256','sourceTrackScope','revision'):
  if key in p:meta[key]=p[key]
 source_spans(data,meta)
 if len(encoded(meta))>1024*1024:raise ValueError('private metadata exceeds consumer limit')
 return meta
