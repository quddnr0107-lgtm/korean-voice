"""Synthetic-only tests: no real key, secret, receiver wrapper, network, or file reads."""
import copy
import unittest
from unittest.mock import patch
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import lecture_crypto as c

class LectureCryptoTests(unittest.TestCase):
    def setUp(self):
        self.source=b'S'*32;self.master=b'M'*32
        private=X25519PrivateKey.from_private_bytes(bytes(range(1,33)))
        pub=c.raw_public(private.public_key());self.p=dict(c.PINNED_PUBLIC,public_key_b64=c.b64(pub),public_sha256=c.digest(pub))
        self.pin=patch.object(c,'PINNED_PUBLIC',self.p);self.pin.start();self.addCleanup(self.pin.stop)
        salt=b'A'*32;nonce=b'B'*12
        # Legacy receiver-wrap encoding/domain remains byte-for-byte compatible.
        raw=private.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption())
        ct=AESGCM(c._derive(self.source,salt,c.LEGACY_INGRESS_VERSION,'receiver-wrap')).encrypt(nonce,raw,c.enc(self.p))
        self.w={'public':self.p,'salt_b64':c.b64(salt),'nonce_b64':c.b64(nonce),'sealed_private_b64':c.b64(ct)}
    def rejects(self,fn,*a,**kw):
        with self.assertRaises((ValueError,InvalidTag)):fn(*a,**kw)
    def test_bounds_and_namespace(self):
        for ident,ns in [('TB-001','tongbang'),('TB-025','tongbang'),('DR-001','directive'),('DR-078','directive'),('RL-001','reserve-law'),('RL-034','reserve-law')]:self.assertEqual(c.namespace_for(ident),'integrated-lectures/v1/'+ns)
        for ident in ['TB-000','TB-026','DR-079','RL-035','TB001','AL-011','TB-01','RL-034x','TB-０１','TB-001\n',None]:self.rejects(c.namespace_for,ident)
    def test_input_roundtrip_all_courses(self):
        for ident in ['TB-025','DR-078','RL-034']:
            rec,ct=c.seal_input(self.p,ident,'비공개 강의 원문'.encode())
            self.assertEqual(c.open_input(self.source,self.p,self.w,rec,ct,ident),'비공개 강의 원문'.encode())
            self.assertEqual(rec['header']['version'],c.INGRESS_VERSION)
    def test_receiver_exact_pin(self):
        for k,v in [('repository','other/repo'),('commit','0'*40),('run_id','1'),('session_id','0'*32),('version',c.INGRESS_VERSION),('public_key_b64',c.b64(b'X'*32))]:
            p=dict(self.p);p[k]=v;self.rejects(c.validate_public,p)
    def test_input_wrong_identity_and_tamper(self):
        rec,ct=c.seal_input(self.p,'TB-001',b'private')
        self.rejects(c.open_input,self.source,self.p,self.w,rec,ct,'TB-002')
        self.rejects(c.open_input,b'Z'*32,self.p,self.w,rec,ct,'TB-001')
        self.rejects(c.open_input,self.source,self.p,self.w,rec,ct[:-1],'TB-001')
        for k,v in [('namespace','integrated-lectures/v1/directive'),('version',c.LEGACY_INGRESS_VERSION),('lesson_id','TB-002'),('bundle_id','f'*32)]:
            r=copy.deepcopy(rec);r['header'][k]=v;self.rejects(c.open_input,self.source,self.p,self.w,r,ct,'TB-001')
        damaged=ct[:-1]+bytes([ct[-1]^1]);r=copy.deepcopy(rec);r['cipher_sha256']=c.digest(damaged)
        self.rejects(c.open_input,self.source,self.p,self.w,r,damaged,'TB-001')
        w=copy.deepcopy(self.w);w['public']=dict(self.p,run_id='1');self.rejects(c.open_input,self.source,self.p,w,rec,ct,'TB-001')
    def test_domain_separation(self):
        material=b'K'*32;salt=b'A'*32
        self.assertNotEqual(c._derive(material,salt,c.INGRESS_VERSION,'input-seal'),c._derive(material,salt,c.LEGACY_INGRESS_VERSION,'input-seal'))
    def test_media_roundtrip_and_range(self):
        plain=bytes(range(256))*700
        for ident in ['TB-001','DR-078','RL-034']:
            m,blob=c.seal(self.master,plain,ident,'audio','test-key')
            out,meta=c.open_range(self.master,m,blob,expected_id=ident,expected_kind='audio',expected_key_id='test-key');self.assertEqual(out,plain);self.assertEqual(meta['status'],200)
            for value,a,b in [('bytes=65530-65550',65530,65551),('bytes=0-0',0,1),('bytes=-10',len(plain)-10,len(plain)),('bytes=170000-',170000,len(plain)),('bytes=0-999999',0,len(plain))]:
                out,meta=c.open_range(self.master,m,blob,value,expected_id=ident);self.assertEqual(out,plain[a:b]);self.assertEqual(meta['status'],206)
    def test_media_identity_and_tamper(self):
        m,blob=c.seal(self.master,b'hello'*20000,'DR-001','private-metadata','test')
        self.rejects(c.open_range,self.master,m,blob,expected_id='DR-002')
        self.rejects(c.open_range,self.master,m,blob,expected_id='DR-001',expected_kind='audio')
        self.rejects(c.open_range,self.master,m,blob,expected_id='DR-001',expected_key_id='other')
        for k,v in [('namespace','integrated-lectures/v1/tongbang'),('lesson_id','DR-002'),('kind','audio'),('key_id','other'),('object_id','f'*32),('plain_bytes',100001),('version','admin-law-premium/v1')]:
            altered=copy.deepcopy(m);altered[k]=v;self.rejects(c.open_range,self.master,altered,blob,expected_id='DR-001')
        self.rejects(c.open_range,self.master,m,blob+b'x',expected_id='DR-001')
        self.rejects(c.open_range,self.master,m,blob[:-1],expected_id='DR-001')
        damaged=bytes([blob[0]^1])+blob[1:];altered=copy.deepcopy(m);altered['cipher_sha256'][0]=c.digest(damaged[:c.CHUNK+16])
        self.rejects(c.open_range,self.master,altered,damaged,expected_id='DR-001')
        self.rejects(c.open_range,b'Z'*32,m,blob,expected_id='DR-001')
    def test_media_validation_and_limits(self):
        self.rejects(c.seal,self.master,b'','TB-001','audio','test')
        self.rejects(c.seal,self.master,b'x','TB-001','source','test')
        self.rejects(c.seal,self.master,b'x','TB-001','audio','TEST')
        self.rejects(c.seal,self.master,b'x','TB-001','audio','test',namespace='integrated-lectures/v1/directive')
        with patch.object(c,'MAX_MEDIA',8):self.rejects(c.seal,self.master,b'123456789','TB-001','audio','test')
        with patch.object(c,'MAX_INPUT',8):self.rejects(c.seal_input,self.p,'TB-001',b'123456789')
        m,blob=c.seal(self.master,b'x','RL-001','audio','test')
        for index in [-1,1,True,1.0]:self.rejects(c.open_chunk,self.master,m,blob,index,expected_id='RL-001')
        m['chunk_bytes']=True;self.rejects(c.validate,m,blob)
    def test_invalid_ranges(self):
        for value in ['bytes=','bytes=-','bytes=-0','bytes=10-','bytes=2-1','bytes=0-1,3-4','bytes=+1-2','bytes= 1-2','bytes=١-٢','bytes='+'9'*200+'-','items=0-1',1]:self.rejects(c.parse_range,value,10)
        for size in [0,-1,True,c.MAX_MEDIA+1]:self.rejects(c.parse_range,None,size)
    def test_checkpoint_explicit_recovery_and_scope(self):
        data=b'FLAC checkpoint zip substitute'*3000
        m,blob=c.seal(self.master,data,'TB-001','checkpoint','test')
        self.rejects(c.open_range,self.master,m,blob,expected_id='TB-001')
        self.rejects(c.open_chunk,self.master,m,blob,0,expected_id='TB-001')
        for kind in ['audio','private-metadata']:
            self.rejects(c.open_range,self.master,m,blob,expected_id='TB-001',expected_kind=kind)
            changed=copy.deepcopy(m);changed['kind']=kind
            self.rejects(c.open_range,self.master,changed,blob,expected_id='TB-001',expected_kind=kind)
        recovered,_=c.open_range(self.master,m,blob,expected_id='TB-001',expected_kind='checkpoint')
        self.assertEqual(recovered,data)
        self.rejects(c.open_range,self.master,m,blob,expected_id='TB-002',expected_kind='checkpoint')
        audio,ab=c.seal(self.master,b'audio','TB-001','audio','test')
        self.rejects(c.open_range,self.master,audio,ab,expected_id='TB-001',expected_kind='checkpoint')
    def test_exact_kind_size_bounds(self):
        self.assertEqual(c.MAX_MEDIA,134217728)
        self.assertEqual(c.MAX_CHECKPOINT,1073741824)
        # Header bounds are checked before encryption/allocation. Avoid 1GiB test buffers.
        m,_=c.seal(self.master,b'x','RL-034','audio','test')
        for kind,limit in [('audio',134217728),('private-metadata',8388608),('checkpoint',1073741824)]:
            q=copy.deepcopy(m);q['kind']=kind;q['plain_bytes']=limit
            c._validate_media_header(q)
            for bad in [0,True,limit+1]:
                q['plain_bytes']=bad;self.rejects(c._validate_media_header,q)
        self.assertEqual(c.parse_range('bytes=-1',1073741824,max_size=c.MAX_CHECKPOINT),(1073741823,1073741823,206))
        self.rejects(c.parse_range,None,1073741824)
        self.rejects(c.parse_range,None,1073741825,max_size=c.MAX_CHECKPOINT)
        # Real encryption follows the same kind-specific cap (small stand-in caps).
        with patch.object(c,'MAX_MEDIA',8),patch.object(c,'MAX_PRIVATE_METADATA',8),patch.object(c,'MAX_CHECKPOINT',16):
            for kind in ['audio','private-metadata']:
                self.rejects(c.seal,self.master,b'123456789','TB-001',kind,'test')
            q,b=c.seal(self.master,b'1234567890123456','TB-001','checkpoint','test')
            self.assertEqual(c.open_range(self.master,q,b,expected_id='TB-001',expected_kind='checkpoint')[0],b'1234567890123456')
            self.rejects(c.seal,self.master,b'12345678901234567','TB-001','checkpoint','test')
    def test_randomness_and_no_cross_object_swap(self):
        m,b=c.seal(self.master,b'hello','TB-001','audio','test');n,d=c.seal(self.master,b'hello','TB-001','audio','test')
        self.assertNotEqual(m['object_id'],n['object_id']);self.assertNotEqual(b,d)
        altered=copy.deepcopy(m);altered['cipher_sha256']=n['cipher_sha256'];self.rejects(c.open_range,self.master,altered,d,expected_id='TB-001')

if __name__=='__main__':unittest.main(verbosity=2)
