"""Narrow correction of pinned FFmpeg 7.0.2 MPEG1 mono44100/128k end-padding.
Only gapless metadata and its CRC change; audio frames are preserved.
Reject unknown encoders, formats, CRCs and length discrepancies."""
def crc16(data):
 c=0
 for b in data:
  c^=b
  for _ in range(8):c=(c>>1)^0xa001 if c&1 else c>>1
 return c

def repair(data,expected,decoded):
 if type(expected) is not int or type(decoded) is not int:raise ValueError('sample count type')
 b=bytearray(data);start=0
 if b[:3]==b'ID3':
  if len(b)<10 or b[3]!=4 or b[4]!=0 or b[5]!=0 or any(x&128 for x in b[6:10]):raise ValueError('ID3 scope')
  start=10+sum(b[6+i]<<(7*(3-i)) for i in range(4))
 # Fixed output produced by current pinned encoder, not a general MP3 parser.
 if bytes(b[start:start+4])!=bytes.fromhex('fffb90c0'):raise ValueError('MPEG scope')
 x=start+21
 if bytes(b[x:x+4])!=b'Info' or bytes(b[x+4:x+8])!=b'\0\0\0\x0f':raise ValueError('Xing scope')
 if b[x+120:x+129]!=b'Lavc61.3.':raise ValueError('encoder scope')
 if len(b)<start+417 or len(b)<x+156:raise ValueError('header truncated')
 if any(b[start+190:start+417]):raise ValueError('header reserved bytes')
 crcpos=x+154;oldcrc=int.from_bytes(b[crcpos:crcpos+2],'big');b[crcpos:crcpos+2]=b'\0\0'
 if crc16(b[start:start+190])!=oldcrc:raise ValueError('header CRC')
 frames=int.from_bytes(b[x+8:x+12],'big')
 if int.from_bytes(b[x+12:x+16],'big')!=len(b)-start or int.from_bytes(b[x+148:x+152],'big')!=len(b)-start:raise ValueError('MP3 size')
 pos=start+417;count=0
 while pos<len(b):
  if pos+4>len(b):raise ValueError('frame truncated')
  h=int.from_bytes(b[pos:pos+4],'big')
  if h>>21!=0x7ff or (h>>19)&3!=3 or (h>>17)&3!=1 or (h>>16)&1!=1 or (h>>12)&15!=9 or (h>>10)&3!=0 or (h>>6)&3!=3:raise ValueError('audio frame scope')
  size=417+((h>>9)&1)
  if pos+size>len(b):raise ValueError('audio frame truncated')
  pos+=size;count+=1
 if count!=frames:raise ValueError('frame count')
 if crc16(b[start+417:])!=int.from_bytes(b[x+152:x+154],'big'):raise ValueError('audio CRC')
 word=int.from_bytes(b[x+141:x+144],'big');delay=word>>12;padding=word&4095
 rem=expected%1152;delta=decoded-expected
 if expected<1152 or not 1<=rem<=46 or delta!=47-rem or delay!=576 or padding!=1681 or frames*1152-delay-padding!=decoded:raise ValueError('not known padding edge')
 if padding+delta>4095:raise ValueError('padding range')
 b[x+141:x+144]=((delay<<12)|(padding+delta)).to_bytes(3,'big')
 b[crcpos:crcpos+2]=crc16(b[start:start+190]).to_bytes(2,'big')
 return bytes(b)
