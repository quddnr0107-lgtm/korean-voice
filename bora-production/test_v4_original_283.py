import hashlib,io,json,os,sys,tempfile,unittest,zipfile
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
import lecture_crypto as crypto,lecture_pack as pack,transport as tr
import v4_original_283 as v
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'local-receiver'))
import open_lesson as receiver

class Batch(unittest.TestCase):
    def test_exact_plan(self):
        groups=v.plan()
        joined='\n'.join(x for a in groups.values() for x in a)+'\n'
        self.assertEqual(hashlib.sha256(joined.encode()).hexdigest(), '3bdd5251bd71261c8732a51474a9548af3556fcd8186c9ee067bcce50cbeac31')
        self.assertEqual(list(map(len,groups.values())),[16]*17+[11])
        self.assertEqual(v.recipient().key_size,3072)

    def test_live_shape_only_fake_audio_all_subjects(self):
        for ident in ['TB-002','RL-027','DR-017','MS-001']:
            with self.subTest(ident=ident),tempfile.TemporaryDirectory() as scratch:
                work=Path(scratch); src=work/'src';src.mkdir()
                key=bytes(range(32));packid='a'*32;mp3=b'synthetic-original-unchanged'
                part,data=pack.seal_part(key,mp3,ident,packid,0,0,44100,0)
                audio=pack.make_pack_manifest(ident,packid,[part],data)
                meta=dict(id=ident,audio_pack_sha256=audio['pack_sha256'],total_samples=44100,
                          sample_rate=44100,narration_text='fixture',segments=[{'text':'fixture'}])
                c,cbytes=crypto.seal(key,json.dumps(meta).encode(),ident,'private-metadata',tr.KEY_ID)
                (src/f'{ident}.audio.{packid}.bin').write_bytes(data)
                (src/f"{ident}.content.{c['object_id']}.bin").write_bytes(cbytes)
                (src/f'{ident}.descriptor.json').write_text(json.dumps(dict(
                    version='integrated-lecture-output/v2',lesson_id=ident,audio=audio,content=c)))
                private=rsa.generate_private_key(public_exponent=65537,key_size=3072)
                path=work/'private';path.write_bytes(private.private_bytes(serialization.Encoding.PEM,
                        serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));path.chmod(0o600)
                cipher=v.seal(private.public_key(),ident,v.recover(src,ident,key))
                envelope=work/'out.zip';filename=ident+'.v4-encrypted.bin'
                with zipfile.ZipFile(envelope,'w') as z:z.writestr(filename,cipher)
                with receiver.lesson(envelope,filename,v.sha(envelope),ident,path) as (m,d,fds):
                    self.assertEqual(m,meta)
                    self.assertEqual(os.read(fds[0],200),mp3)
                with self.assertRaises(OSError):os.fstat(fds[0])
                self.assertEqual(sorted(p.name for p in work.iterdir()),['out.zip','private','src'])
                with self.assertRaises(ValueError):
                    with receiver.lesson(envelope,filename,'0'*64,ident,path):pass

if __name__=='__main__':unittest.main()
