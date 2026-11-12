"""Private manuscript -> paragraph-bounded synthesis plan / measured site cues.
No authentication, network, upload, deletion, or automatic deployment.
The current integrated lecture UI (examPrep) uses EVERY nonempty newline-delimited
line as a <p>, not just double-newline paragraphs. Keep its raw UTF-16 offsets.
"""
import argparse, hashlib, json, re
from pathlib import Path
from quality import prepare

FORMAT='kim-bora-production-paragraph-plan/v1'
JS_SPACE='\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680'+''.join(chr(x) for x in range(0x2000,0x200b))+'\u2028\u2029\u202f\u205f\u3000\ufeff'

def utf16(text):return len(text.encode('utf-16-le'))//2
def digest(text):return hashlib.sha256(text.encode()).hexdigest()

def _paragraphs(source):
    visible=[]
    for match in re.finditer(r'[^\n]+',source):
        line=match[0];text=line.strip(JS_SPACE)
        if not text:continue
        lead=len(line)-len(line.lstrip(JS_SPACE))
        tail=len(line.rstrip(JS_SPACE))
        visible.append((match.start(),match.start()+lead,match.start()+tail))
    if not visible:raise ValueError('empty manuscript')
    for i,(line_start,start,end) in enumerate(visible):
        a=0 if i==0 else line_start
        b=visible[i+1][0] if i+1<len(visible) else len(source)
        yield a,b,start,end

def _cuts(source,a,b,limit):
    """Prefer sentence endings; a too-long sentence uses word boundaries.
    An unbroken long token stays intact and receives an explicit review flag.
    """
    cursor=a
    while b-cursor>limit:
        window=source[cursor:cursor+limit]
        ends=[m.end() for m in re.finditer(r'[.!?。](?:[ \t]+|$)',window)]
        if ends:cut=cursor+ends[-1]
        else:
            gaps=[m.end() for m in re.finditer(r'[ \t]+',window)]
            if not gaps:break
            cut=cursor+gaps[-1]
        if not source[cursor:cut].strip(JS_SPACE):break
        yield cursor,cut;cursor=cut
    if cursor<b:yield cursor,b

def build_plan(source,lesson_id='TB-001',max_chars=220,reading_spans=()):
    if not isinstance(source,str) or not source.strip(JS_SPACE):raise ValueError('empty manuscript')
    from lecture_crypto import namespace_for
    namespace_for(lesson_id)
    if not isinstance(max_chars,int) or not 40<=max_chars<=400:raise ValueError('chunk bound')
    boundaries={utf16(source[:i]):i for i in range(len(source)+1)}
    spans=[];previous=0
    for pair in reading_spans:
        a,b=pair
        if a not in boundaries or b not in boundaries or a>=b or a<previous:raise ValueError('invalid/overlapping UTF-16 reading span')
        spans.append((boundaries[a],boundaries[b]));previous=b
    paragraphs=[];units=[]
    for pi,(a,b,start,end) in enumerate(_paragraphs(source)):
        paragraph_id=f'{lesson_id}:p{pi:04d}'
        paragraphs.append(dict(id=paragraph_id,start_utf16=utf16(source[:a]),end_utf16=utf16(source[:b]),
                  visible_start_utf16=utf16(source[:start]),visible_end_utf16=utf16(source[:end]),source=source[a:b]))
        for x,y in spans:
            if x<end and y>start and not start<=x<y<=end:raise ValueError('reading span crosses paragraph boundary')
        cuts=sorted({start,end}|{x for pair in spans for x in pair if start<x<end})
        for x,y in zip(cuts,cuts[1:]):
            mode='reading' if any(lo<=x and y<=hi for lo,hi in spans) else 'explain'
            for lo,hi in _cuts(source,x,y,max_chars):
                text=source[lo:hi]
                if not text.strip(JS_SPACE):continue
                norm=prepare(text.strip(JS_SPACE))
                flags=list(norm['review'])
                if len(text)>max_chars:flags.append('long_unbroken_span_review')
                units.append(dict(id=f'{paragraph_id}:u{sum(u["paragraph_id"]==paragraph_id for u in units):03d}',
                  paragraph_id=paragraph_id,mode=mode,start_utf16=utf16(source[:lo]),end_utf16=utf16(source[:hi]),
                  source=text,source_sha256=digest(text),spoken=norm['spoken'],review=flags))
        # Keep separator whitespace in raw coverage, as the existing producer
        # contract does. It is stripped ONLY from spoken text, never the source.
        own=[u for u in units if u['paragraph_id']==paragraph_id]
        starts=[a]+[boundaries[u['start_utf16']] for u in own[1:]]
        ends=starts[1:]+[b]
        for unit,lo,hi in zip(own,starts,ends):
            raw=source[lo:hi];norm=prepare(raw.strip(JS_SPACE))
            unit.update(start_utf16=utf16(source[:lo]),end_utf16=utf16(source[:hi]),
                        source=raw,source_sha256=digest(raw),spoken=norm['spoken'])
    if ''.join(p['source'] for p in paragraphs)!=source:raise ValueError('paragraph source coverage')
    if ''.join(u['source'] for u in units)!=source:raise ValueError('unit source coverage')
    return dict(schema=FORMAT,lesson_id=lesson_id,source_sha256=digest(source),source_utf16_length=utf16(source),
                source=source,paragraphs=paragraphs,units=units,merge_across_paragraphs=False,
                production_status='not_uploaded_or_activated')

def measured_timeline(plan,measurements):
    """Measurements must describe FINAL decoded/trimmed/resampled PCM units.
    We intentionally do not estimate times from text length or old recordings.
    """
    if plan.get('schema')!=FORMAT or digest(plan['source'])!=plan['source_sha256']:raise ValueError('source integrity')
    source=plan['source'];positions={utf16(source[:i]):i for i in range(len(source)+1)}
    if plan['source_utf16_length']!=utf16(source):raise ValueError('source length')
    owners={p['id']:p for p in plan['paragraphs']}
    cursor=0
    for unit in plan['units']:
        a,b=unit['start_utf16'],unit['end_utf16'];owner=owners.get(unit['paragraph_id'])
        if a!=cursor or b<=a or a not in positions or b not in positions:raise ValueError('unit coverage')
        if not owner or not owner['start_utf16']<=a<b<=owner['end_utf16']:raise ValueError('cross-paragraph unit')
        raw=source[positions[a]:positions[b]]
        if raw!=unit['source'] or digest(raw)!=unit['source_sha256']:raise ValueError('unit source integrity')
        cursor=b
    if cursor!=utf16(source):raise ValueError('unit coverage')
    if measurements.get('source_sha256')!=plan['source_sha256']:raise ValueError('stale measurement source')
    rows=measurements['units']
    if len(rows)!=len(plan['units']) or len({r['id'] for r in rows})!=len(rows):raise ValueError('missing/duplicate measurements')
    by_id={r['id']:r for r in rows}
    if set(by_id)!={u['id'] for u in plan['units']}:raise ValueError('measurement identity')
    segments=[];clock=0;sample_rate=None
    for unit in plan['units']:
        row=by_id[unit['id']]
        if row.get('measurement_kind')!='final_decoded_pcm':raise ValueError('unmeasured timing')
        if row.get('source_sha256')!=unit['source_sha256']:raise ValueError('stale unit audio')
        rate=row['sample_rate'];count=row['sample_count'];gap=row.get('gap_before_samples',0)
        if type(rate)!=int or rate<=0 or type(count)!=int or count<=0 or type(gap)!=int or gap<0:raise ValueError('invalid sample measurement')
        if sample_rate is not None and rate!=sample_rate:raise ValueError('mixed sample rates')
        sample_rate=rate;clock+=gap
        segments.append(dict(paragraph_id=unit['paragraph_id'],unit_id=unit['id'],
          start_utf16=unit['start_utf16'],end_utf16=unit['end_utf16'],start_s=clock/rate,end_s=(clock+count)/rate,
          start_sample=clock,end_sample=clock+count))
        clock+=count
    return dict(lesson_id=plan['lesson_id'],source_sha256=plan['source_sha256'],segments=segments,
                sample_rate=sample_rate,total_samples=clock,duration_s=clock/sample_rate,
                status='timing_prepared_not_deployed')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('source',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--lesson-id',default='TB-001');parser.add_argument('--max-chars',type=int,default=220)
    parser.add_argument('--reading-spans',type=Path,help='reviewed [start_utf16,end_utf16] pairs, within paragraphs')
    parser.add_argument('--measurements',type=Path,help='requires an existing plan JSON as source')
    args=parser.parse_args()
    if args.measurements:out=measured_timeline(json.loads(args.source.read_text()),json.loads(args.measurements.read_text()))
    else:
        data=json.loads(args.source.read_text())
        if isinstance(data,dict) and data.get('id',args.lesson_id)!=args.lesson_id:raise ValueError('lesson id mismatch')
        text=data.get('lectureScript') if isinstance(data,dict) else data
        if not isinstance(text,str):raise ValueError('expected lectureScript string')
        spans=json.loads(args.reading_spans.read_text()) if args.reading_spans else []
        out=build_plan(text,args.lesson_id,args.max_chars,spans)
    with args.output.open('x',encoding='utf-8') as file:
        args.output.chmod(0o600);json.dump(out,file,ensure_ascii=False,indent=2)
    print(json.dumps(dict(status=out.get('status','private_plan_prepared'),lesson_id=out['lesson_id'])))

