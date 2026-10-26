"""Only merge the original continuous span of chunks 0 and 1. No text rewrite."""
def merge_header_first_body(text,chunks):
 if len(chunks)<2:raise ValueError('header/body chunks required')
 cursor=0;bounds=[]
 for part in chunks:
  while cursor<len(text) and text[cursor].isspace():cursor+=1
  if not part or text[cursor:cursor+len(part)]!=part:raise ValueError('original chunks exact coverage')
  bounds.append((cursor,cursor+len(part)));cursor+=len(part)
 if text[cursor:].strip():raise ValueError('original chunks uncovered tail')
 start,end=bounds[0][0],bounds[1][1];merged=text[start:end]
 if len(merged)>220 or '\n\n' not in text[bounds[0][1]:bounds[1][0]]:raise ValueError('merged header boundary/length')
 if merged!=chunks[0]+text[bounds[0][1]:bounds[1][0]]+chunks[1]:raise ValueError('contiguous merge')
 return [merged]+chunks[2:]


def original_chunks(text):
 """Byte-for-text equivalent to the reviewed original split, before merging."""
 import re
 chunks=[]
 for c in [x.strip() for x in re.split(r'(?<=[.!?])\s+|\n\s*\n',text) if x.strip()]:
  while len(c)>220:
   cut=c.rfind(' ',0,220)
   if cut<=0:raise ValueError('chunk boundary')
   chunks.append(c[:cut]);c=c[cut:].strip()
  if c:chunks.append(c)
 if not 0<len(chunks)<150 or ''.join(''.join(chunks).split())!=''.join(text.split()):raise ValueError('chunk preservation')
 return chunks

def expected_chunks(text):
 return merge_header_first_body(text,original_chunks(text))
