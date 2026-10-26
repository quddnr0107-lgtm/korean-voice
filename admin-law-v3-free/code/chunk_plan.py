"""Pack contiguous original spans; never invent pauses, text or timing."""
import re

def chunks(text):
 units=[]
 for m in re.finditer(r'\S(?:.*?\S)?(?=\s*(?:\n\s*\n|(?<=[.!?])\s+)|\s*$)',text,re.S):
  a,b=m.span()
  while b-a>220:
   cut=text.rfind(' ',a,a+220)
   if cut<=a:raise ValueError('no safe split')
   units.append((a,cut));a=cut
   while a<b and text[a].isspace():a+=1
  if a<b:units.append((a,b))
 # Validate original-unit full coverage before packing.
 cursor=0
 for a,b in units:
  if text[cursor:a].strip():raise ValueError('unit coverage')
  cursor=b
 if text[cursor:].strip() or not units:raise ValueError('unit tail')
 packed=[];a,b=units[0]
 for c,d in units[1:]:
  if d-a<=180:b=d
  else:packed.append(text[a:b]);a,b=c,d
 packed.append(text[a:b]);verify(text,packed);return packed

def verify(text,parts):
 cursor=0
 if not 0<len(parts)<150:raise ValueError('chunk count')
 for part in parts:
  while cursor<len(text) and text[cursor].isspace():cursor+=1
  if not part or len(part)>220 or text[cursor:cursor+len(part)]!=part:raise ValueError('exact chunk')
  cursor+=len(part)
 if text[cursor:].strip():raise ValueError('chunk tail')
 return True
