import unittest
from mp3_gapless import repair,crc16

def fixture():
 # Synthetic MPEG-shaped bytes, not lecture audio; validates parser/CRC contracts.
 data=bytearray(bytes.fromhex('fffb90c0')+bytes(413))
 for _ in range(22):data+=bytes.fromhex('fffb90c0')+bytes(413)
 x=21;data[x:x+4]=b'Info';data[x+4:x+8]=(15).to_bytes(4,'big');data[x+8:x+12]=(22).to_bytes(4,'big')
 for pos in [x+12,x+148]:data[pos:pos+4]=len(data).to_bytes(4,'big')
 data[x+120:x+129]=b'Lavc61.3.';data[x+141:x+144]=((576<<12)|1681).to_bytes(3,'big')
 data[x+152:x+154]=crc16(data[417:]).to_bytes(2,'big');data[x+154:x+156]=crc16(data[:190]).to_bytes(2,'big')
 return bytes(data)
class Gapless(unittest.TestCase):
 def test_all_known_remainders_exact_padding_frames_unchanged(self):
  original=fixture()
  for rem in range(1,47):
   expected=23040+rem;fixed=repair(original,expected,23087)
   self.assertEqual(fixed[417:],original[417:]);self.assertEqual(len(fixed),len(original))
   word=int.from_bytes(fixed[162:165],'big');self.assertEqual(22*1152-(word>>12)-(word&4095),expected)
   h=bytearray(fixed[:190]);stored=int.from_bytes(h[175:177],'big');h[175:177]=b'\0\0';self.assertEqual(crc16(h),stored)
 def test_unrecognized_counts(self):
  for expected,decoded in [(23040,23040),(23041,23086),(23041,23088),(1,47),(True,23087),(23041.0,23087)]:
   with self.assertRaises(ValueError):repair(fixture(),expected,decoded)
 def test_no_second_repair(self):
  with self.assertRaises(ValueError):repair(repair(fixture(),23041,23087),23041,23087)
 def test_corruption_rejected(self):
  for i in [0,3,21,25,29,33,141,162,165,175,400,500,len(fixture())-1]:
   data=bytearray(fixture());data[i]^=1
   with self.subTest(i=i),self.assertRaises(ValueError):repair(data,23041,23087)
 def test_truncation_rejected(self):
  for n in [0,5,10,100,190,417,len(fixture())-1]:
   with self.assertRaises(ValueError):repair(fixture()[:n],23041,23087)
