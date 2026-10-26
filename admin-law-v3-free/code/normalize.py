"""Exact reversible TTS edits. No source deletion or legal paraphrase."""
import re,hashlib,unicodedata
D='영일이삼사오육칠팔구'
def sino(n):
 n=int(n)
 if n<0 or n>=10**20:raise ValueError('numeric range')
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
P=re.compile(r'(?P<date>\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\.)|(?P<case>\d{2,4}(?:헌가|헌바|헌마|헌사|헌라|누|두|다|도|고합|구합|구단|모|마)\d+)|(?P<number>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d+)|(?P<circled>[①-⑳])|(?P<symbol>㎡|㎢|㎥|%|(?<![A-Za-z])ha(?![A-Za-z]))')
def replace(m):
 t=m[0];rule=m.lastgroup
 if rule=='date':
  y,mo,d=map(int,re.findall(r'\d+',t))
  import datetime;datetime.date(y,mo,d)
  month={6:'유',10:'시'}.get(mo,sino(mo))
  return f'{sino(y)} 년 {month} 월 {sino(d)} 일',rule
 if rule=='case':
  q=re.fullmatch(r'(\d+)(\D+)(\d+)',t);return sino(q[1])+' '+q[2]+' '+sino(q[3]),rule
 if rule=='number':
  cleaned=t.replace(',','')
  if '.' in cleaned:
   a,b=cleaned.split('.');return sino(a)+' 점 '+''.join(D[int(c)] for c in b),rule
  if m.string[m.end():].startswith('월') and int(cleaned) in (6,10):return {6:'유',10:'시'}[int(cleaned)],rule
  return sino(cleaned),rule
 if rule=='circled':return sino(ord(t)-ord('①')+1)+'. ',rule
 return {'㎡':'제곱미터','㎢':'제곱킬로미터','㎥':'세제곱미터','%':'퍼센트','ha':'헥타르'}[t],rule

def normalize(source):
 parts=[];changes=[];cursor=0
 for m in P.finditer(source):
  value,rule=replace(m);parts.extend((source[cursor:m.start()],value));cursor=m.end()
  changes.append({'source_start':m.start(),'source_end':m.end(),'original':m[0],'replacement':value,'rule':rule})
 parts.append(source[cursor:]);n=''.join(parts)
 verify(source,n,changes)
 if re.search(r'[0-9①-⑳\u3400-\u9fff]',n):raise ValueError('unreviewed character remains')
 return n,changes

def verify(source,narration,changes):
 pos=0;pieces=[]
 for c in changes:
  a,b=c['source_start'],c['source_end']
  if not pos<=a<b<=len(source) or source[a:b]!=c['original']:raise ValueError('edit source span')
  m=P.match(source,a)
  if m is None or m.end()!=b or replace(m)!=(c['replacement'],c['rule']):raise ValueError('edit rule mismatch')
  pieces.extend((source[pos:a],c['replacement']));pos=b
 pieces.append(source[pos:])
 if ''.join(pieces)!=narration:raise ValueError('edit reconstruction')
 reverse=narration;delta=0;edits=[]
 for c in changes:
  a=c['source_start']+delta;edits.append((a,a+len(c['replacement']),c['original']));delta+=len(c['replacement'])-len(c['original'])
 for a,b,old in reversed(edits):reverse=reverse[:a]+old+reverse[b:]
 if reverse!=source:raise ValueError('reverse reconstruction')
 return True
