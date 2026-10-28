"""Conservative reversible pronunciation edits; source/legal wording never mutated.
normalize(source) -> (spoken_text, edits, review_flags).
Ambiguous notation is retained verbatim and flagged, not silently interpreted.
"""
import datetime
import re
D='영일이삼사오육칠팔구'
RULESET='lecture-tts-normalize/v1'
ROMAN={'Ⅰ':'일','Ⅱ':'이','Ⅲ':'삼','Ⅳ':'사','Ⅴ':'오','Ⅵ':'육','Ⅶ':'칠','Ⅷ':'팔','Ⅸ':'구','Ⅹ':'십'}
LETTERS=dict(zip('ABCDEFGHIJKLMNOPQRSTUVWXYZ',['에이','비','씨','디','이','에프','지','에이치','아이','제이','케이','엘','엠','엔','오','피','큐','알','에스','티','유','브이','더블유','엑스','와이','지']))
NATIVE_ONES=['','한','두','세','네','다섯','여섯','일곱','여덟','아홉']
NATIVE_TENS=['','열','스물','서른','마흔','쉰','예순','일흔','여든','아흔']
# Existing approved ko-voice.js unit inventory. Longest match prevents 개/개월,
# 번/번지, 대/대대, and 시/시간 ambiguity. Clause ordinals retain Sino reading.
NATIVE_UNITS=set('시간 번째 군데 켤레 그릇 봉지 상자 송이 자루 포기 바퀴 걸음 가지 사람 명 개 살 시 번 마리 잔 병 장 권 대 벌 채 척 곳 달 줄 통 판 알 조각 방 끼 컵 캔 쌍 팀 과목 문제 편 곡 박스 묶음 꾸러미 다발 타'.split())
SINO_UNITS=set('개월 주일 학년 학기 등급 호선 번지 페이지 인분 사단 여단 연대 대대 중대 소대 분대 킬로미터 킬로그램 센티미터 밀리미터 킬로칼로리 퍼센트 밀리리터 리터 미터 그램 년 월 일 분 초 원 층 호 회 기 등 급 주 차 조 항 동 반 세 점 위 쪽 인 박 부 건 종 류 배 도 차원 차선 단 군 기수 기간 차례'.split())
UNIT_PATTERN='|'.join(re.escape(x) for x in sorted(NATIVE_UNITS|SINO_UNITS,key=lambda x:(-len(x),x)))


def sino(n):
    n=int(n)
    if not 0<=n<10**20:raise ValueError('numeric range')
    if n==0:return '영'
    def small(x):
        out=''
        for v,u in ((1000,'천'),(100,'백'),(10,'십'),(1,'')):
            q,x=divmod(x,v)
            if q:out+=('' if q==1 and v>1 else D[q])+u
        return out
    parts=[]
    for power,unit in ((16,'경'),(12,'조'),(8,'억'),(4,'만'),(0,'')):
        q,n=divmod(n,10**power)
        if q:parts.append(('' if q==1 and power==4 else small(q))+unit)
    return ' '.join(parts)

def native(n):
    if not 1<=n<=99:return sino(n)
    if n==20:return '스무'
    return NATIVE_TENS[n//10]+NATIVE_ONES[n%10]

def clock_hour(n):return native(n) if 1<=n<=12 else sino(n)
def month(n):return {6:'유월',10:'시월'}.get(n,sino(n)+'월')

PATTERN=re.compile(
    r'(?P<date>[0-9]{4}\.\s*[0-9]{1,2}\.\s*[0-9]{1,2}\.)'
    r'|(?P<datewords>[0-9]{4}\s*년\s*[0-9]{1,2}\s*월\s*[0-9]{1,2}\s*일)'
    r'|(?P<case>[0-9]{2,4}(?:헌가|헌바|헌마|헌사|헌라|누|두|다|도|고합|구합|구단|모|마)[0-9]+)'
    r'|(?P<mixed>(?<![A-Za-z0-9])(?=[A-Za-z0-9-]*[A-Za-z])(?=[A-Za-z0-9-]*[0-9])[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*(?![A-Za-z0-9]))'
    r'|(?P<latin>[A-Za-z]+)'
    r'|(?P<han>[\u3400-\u9fff]+)'
    r'|(?P<clock>[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)'
    r'|(?P<range>[0-9]+(?:\.[0-9]+)?\s*[~～〜∼–—/\-]\s*[0-9]+(?:\.[0-9]+)?(?:\s*(?:시간|개|%|[hH]|[kK][mM]))?)'
    r'|(?P<duration>[0-9]+\s*시간)'
    r'|(?P<hour>[0-9]+\s*시(?!간))'
    r'|(?P<month>[0-9]+\s*월)'
    r'|(?P<roman>[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+형?)'
    r'|(?P<quantity>[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?\s*(?:'+UNIT_PATTERN+r')|[0-9]+(?:\.[0-9]+)?\s*(?:'+UNIT_PATTERN+r'))'
    r'|(?P<number>[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+\.[0-9]+|[0-9]+)'
    r'|(?P<circled>[①-⑳])'
    r'|(?P<symbol>㎡|㎢|㎥|%|(?<![A-Za-z])ha(?![A-Za-z]))')

def replacement(m):
    t=m[0];rule=m.lastgroup
    if rule=='mixed':
        reviewed={'C4I':'씨포아이','M16':'엠십육','M60':'엠육십','M1':'엠원','M2':'엠투'}
        if t in reviewed:return reviewed[t],'reviewed-equipment-alias',None
        # Explicit mappings already present in the approved ko-voice.js recipe.
        approved={'K1':'케이원','K1A1':'케이원 에이원','K2':'케이투','K2A1':'케이투 에이원','K9':'케이나인','K9A1':'케이나인 에이원','K-1':'케이원','K-2':'케이투','K-9':'케이나인','K-1A1':'케이원 에이원','K-2A1':'케이투 에이원','K-9A1':'케이나인 에이원'}
        if t in approved:return approved[t],'approved-equipment-alias',None
        unit=re.fullmatch(r'([0-9]+)([kK][mM]|[hH])',t)
        if unit:
            value=int(unit[1]);hours=unit[2].lower()=='h'
            return (native(value)+' 시간' if hours else sino(value)+' 킬로미터'),'approved-unit-alias',None
        return t,None,'ambiguous-mixed'
    if rule=='range':
        q=re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)\s*[~～〜∼]\s*([0-9]+(?:\.[0-9]+)?)(?:\s*(시간|개|%|[hH]|[kK][mM]))?',t)
        if not q:return t,None,'ambiguous-range'
        unit=q[3] or '';unit={'h':'시간','km':'킬로미터','%':'퍼센트'}.get(unit.lower(),unit)
        def read_endpoint(v):
            if '.' in v:
                a,b=v.split('.');value=sino(a)+' 점 '+''.join(D[int(ch)] for ch in b)
            else:value=native(int(v)) if unit in ('시간','개') else sino(v)
            return value+(' '+unit if unit else '')
        return read_endpoint(q[1])+'에서 '+read_endpoint(q[2]),'approved-numeric-range',None
    if rule=='latin':
        if t.isupper() and len(t)<=6:return ''.join(LETTERS[c] for c in t),'approved-uppercase-spelling',None
        return t,None,'english-word-review'
    if rule=='han':
        if t=='立射壕' and re.search(r'입사호\s*[(]\s*$',m.string[:m.start()]):return '입사호','source-explicit-han-reading',None
        return t,None,'han-reading-review'
    if rule in ('date','datewords'):
        y,mo,d=map(int,re.findall('[0-9]+',t))
        try:datetime.date(y,mo,d)
        except ValueError:return t,None,'invalid-date'
        return f'{sino(y)}년 {month(mo)} {sino(d)}일','calendar-date',None
    if rule=='case':
        q=re.fullmatch(r'([0-9]+)([^0-9]+)([0-9]+)',t)
        return sino(q[1])+' '+q[2]+' '+sino(q[3]),'case-reference',None
    if rule=='clock':
        nums=list(map(int,t.split(':')))
        if not 0<=nums[0]<=23 or any(not 0<=v<=59 for v in nums[1:]):return t,None,'invalid-clock'
        out=clock_hour(nums[0])+' 시'
        if nums[1]:out+=' '+sino(nums[1])+' 분'
        if len(nums)==3 and nums[2]:out+=' '+sino(nums[2])+' 초'
        return out,'clock-24h-preserved',None
    if rule=='duration':
        n=int(re.search('[0-9]+',t)[0]);return native(n)+' 시간','duration-hours-native',None
    if rule=='hour':
        n=int(re.search('[0-9]+',t)[0])
        if not 0<=n<=24:return t,None,'invalid-hour'
        return clock_hour(n)+' 시','hour-24h-preserved',None
    if rule=='month':
        n=int(re.search('[0-9]+',t)[0])
        if not 1<=n<=12:return t,None,'invalid-month'
        return month(n),'calendar-month',None
    if rule=='roman':
        base=t[:-1] if t.endswith('형') else t
        if len(base)!=1 or base not in ROMAN or not t.endswith('형'):return t,None,'ambiguous-roman'
        return ROMAN[base]+'형','roman-type',None
    if rule=='quantity':
        q=re.fullmatch(r'([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)(\s*)('+UNIT_PATTERN+r')',t)
        raw,gap,unit=q[1].replace(',',''),q[2],q[3]
        ordinal=re.search(r'제\s*$',m.string[:m.start()]) is not None
        if '.' in raw:
            a,b=raw.split('.');value=sino(a)+' 점 '+''.join(D[int(c)] for c in b)
            return value+' '+unit,'decimal-quantity',None
        num=int(raw)
        if ordinal:return sino(num)+gap+unit,'sino-ordinal-unit',None
        value=native(num) if unit in NATIVE_UNITS and 1<=num<=99 else sino(num)
        return value+' '+unit,('native-quantity' if unit in NATIVE_UNITS and 1<=num<=99 else 'sino-quantity'),None
    if rule=='number':
        cleaned=t.replace(',','')
        if len(cleaned.split('.')[0])>20:return t,None,'large-number'
        if '.' in cleaned:
            a,b=cleaned.split('.');return sino(a)+' 점 '+''.join(D[int(c)] for c in b),'decimal-digits',None
        return sino(cleaned),'sino-number',None
    if rule=='circled':return sino(ord(t)-ord('①')+1),'circled-number',None
    return {'㎡':'제곱미터','㎢':'제곱킬로미터','㎥':'세제곱미터','%':'퍼센트','ha':'헥타르'}[t],'measurement-symbol',None

def _scan(source):
    if not isinstance(source,str):raise ValueError('source type')
    parts=[];edits=[];flags=[];cursor=0;delta=0
    for m in PATTERN.finditer(source):
        try:value,rule,flag=replacement(m)
        except ValueError:value,rule,flag=m[0],None,'unsupported-number'
        parts.extend((source[cursor:m.start()],value));cursor=m.end()
        if flag:flags.append({'source_start':m.start(),'source_end':m.end(),'original':m[0],'reason':flag})
        if value!=m[0]:
            a=m.start()+delta
            edits.append({'source_start':m.start(),'source_end':m.end(),'spoken_start':a,'spoken_end':a+len(value),'original':m[0],'replacement':value,'rule':rule})
            delta+=len(value)-len(m[0])
    parts.append(source[cursor:]);return ''.join(parts),edits,flags

def verify(source,spoken,edits):
    expected,expected_edits,_=_scan(source)
    if expected!=spoken or expected_edits!=edits:raise ValueError('normalization recipe/span mismatch')
    reverse=spoken
    for e in reversed(edits):
        a,b=e['spoken_start'],e['spoken_end']
        if reverse[a:b]!=e['replacement']:raise ValueError('replacement span')
        reverse=reverse[:a]+e['original']+reverse[b:]
    if reverse!=source:raise ValueError('reverse reconstruction')
    return True

def normalize(source):
    spoken,edits,flags=_scan(source);verify(source,spoken,edits)
    # Idempotence is a release invariant. Ambiguous unchanged tokens stay flagged.
    again,new_edits,_=_scan(spoken)
    if again!=spoken or new_edits:raise ValueError('normalization not idempotent')
    return spoken,edits,flags
