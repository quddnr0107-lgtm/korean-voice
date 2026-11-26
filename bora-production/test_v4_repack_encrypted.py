import hashlib,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import v4_repack_encrypted as r

class Tests(unittest.TestCase):
    def test_expected_only_and_per_chunk_size(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); z=p/'upstream.zip';root=p/'out';root.mkdir()
            ids=['TB-002','TB-003','TB-004']
            names=[i+'.v4-encrypted.bin' for i in ids]
            with zipfile.ZipFile(z,'w') as f:
                for i,n in enumerate(names):f.writestr(n,bytes([i])*800)
            sha='sha256:'+hashlib.sha256(z.read_bytes()).hexdigest()
            with patch.object(r,'MAX',1700):
                self.assertEqual(r.split(z,root,ids,z.stat().st_size,sha),2)
            self.assertEqual((root/'01'/names[0]).read_bytes(),b'\0'*800)
            self.assertEqual((root/'02'/names[2]).read_bytes(),b'\2'*800)
            with self.assertRaises(ValueError):r.split(z,root,ids[:-1],z.stat().st_size,sha)
            with self.assertRaises(ValueError):r.split(z,root,ids,z.stat().st_size,'sha256:'+'0'*64)


    def test_official_path_no_copy_or_cipher_change(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);root=p/'src';root.mkdir();out=p/'out'
            ids=['TB-002','TB-003','TB-004']
            names=[i+'.v4-encrypted.bin' for i in ids]
            for i,n in enumerate(names):(root/n).write_bytes(bytes([i])*800)
            with patch.object(r,'MAX',1700):
                self.assertEqual(r.split_downloaded(root,out,ids),2)
            self.assertEqual((root/names[2]).stat().st_ino,(out/'02'/names[2]).stat().st_ino)
            with self.assertRaises(ValueError):r.split_downloaded(root,p/'reject',ids[:-1])
