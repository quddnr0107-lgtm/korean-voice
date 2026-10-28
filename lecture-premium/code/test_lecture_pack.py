"""Public synthetic-only segmented-media cryptographic contract tests."""
import copy,unittest
from unittest.mock import patch
from cryptography.exceptions import InvalidTag
import lecture_pack as p

class PackTests(unittest.TestCase):
    def setUp(self):self.key=bytes(range(32));self.pid='a'*32
    def rejects(self,fn,*a,**kw):
        with self.assertRaises((ValueError,InvalidTag)):fn(*a,**kw)
    def part(self,data=b'synthetic-only',index=0,start=0,end=44100,offset=0,ident='TB-001'):
        return p.seal_part(self.key,data,ident,self.pid,index,start,end,offset)
    def pack(self,ident='TB-001'):
        first=b'A'*70001;second=b'B'*33
        a,x=self.part(first,ident=ident);b,y=self.part(second,index=1,start=44100,end=88200,offset=len(x),ident=ident)
        return p.make_pack_manifest(ident,self.pid,[a,b],x+y),x+y,[first,second]
    def open(self,m,blob,**overrides):
        args={'expected_id':m['lesson_id'],'expected_part_index':m['part_index'],'expected_pack_id':m['pack_id']};args.update(overrides)
        return p.open_part(self.key,m,blob,**args)
    def test_consumer_limits(self):
        self.rejects(self.part,index=1440)
        self.rejects(self.part,start=p.MAX_TOTAL_SAMPLES-1,end=p.MAX_TOTAL_SAMPLES+1)
        m,b,_=self.pack();m['total_samples']=p.MAX_TOTAL_SAMPLES+1;self.rejects(p.validate_pack,m)
        m,b,_=self.pack();m['parts']=[m['parts'][0]]*1441;self.rejects(p.validate_pack,m)
    def test_roundtrip_three_courses_multichunk(self):
        for ident in ['TB-025','DR-078','RL-034']:
            m,blob,plains=self.pack(ident);p.validate_pack(m);p.validate_pack(m,blob)
            for part,plain in zip(m['parts'],plains):
                cipher=blob[part['pack_offset']:part['pack_offset']+part['cipher_bytes']]
                self.assertEqual(self.open(part,cipher),plain)
    def test_context_order_and_aad_contract(self):
        m,b=self.part()
        self.assertEqual(p.part_context(m),[p.PART_VERSION,m['key_id'],m['namespace'],m['lesson_id'],m['kind'],m['object_id'],m['pack_id'],m['part_index'],m['start_sample'],m['end_sample'],m['sample_rate'],m['pack_offset'],m['cipher_bytes'],m['plain_bytes']])
        self.assertEqual(p.part_aad(m,0),p.enc(p.part_context(m)+[0,65536]))
    def test_expected_scope_required(self):
        m,b=self.part()
        self.rejects(self.open,m,b,expected_id='TB-002')
        self.rejects(self.open,m,b,expected_part_index=1)
        self.rejects(self.open,m,b,expected_pack_id='b'*32)
        self.rejects(self.open,m,b,expected_part_index=True)
        self.rejects(p.open_part,b'X'*32,m,b,expected_id='TB-001',expected_part_index=0,expected_pack_id=self.pid)
    def test_coordinates_authenticated_not_only_structurally_checked(self):
        m,b=self.part()
        mutations=[{'part_index':1},{'pack_offset':1},{'start_sample':1,'end_sample':44101},{'end_sample':44101},{'pack_id':'b'*32},{'object_id':'b'*32},{'lesson_id':'TB-002'}]
        for update in mutations:
            changed=dict(m,**update);p.validate_part(changed,b)
            # Even with the caller matching the modified expected identity, AEAD fails.
            self.rejects(self.open,changed,b)
    def test_cipher_tamper_even_with_recomputed_public_hashes(self):
        m,b=self.part(b'x'*70000);bad=bytes([b[0]^1])+b[1:];changed=copy.deepcopy(m)
        changed['cipher_sha256'][0]=p.digest(bad[:p.CHUNK+16]);p.validate_part(changed,bad)
        self.rejects(self.open,changed,bad)
        self.rejects(self.open,m,b[:-1]);self.rejects(self.open,m,b+b'x')
    def test_pack_digest_truncation_and_unexpected_fields(self):
        m,b,_=self.pack()
        self.rejects(p.validate_pack,m,b[:-1]);self.rejects(p.validate_pack,m,b+b'x')
        x=copy.deepcopy(m);x['extra']='not allowed';self.rejects(p.validate_pack,x,b)
        x=copy.deepcopy(m);x['parts'][0]['extra']='not allowed';self.rejects(p.validate_pack,x,b)
        bad=bytes([b[0]^1])+b[1:];x=copy.deepcopy(m);x['pack_sha256']=p.digest(bad)
        self.rejects(p.validate_pack,x,bad)
    def test_pack_swap_duplicate_gap_overlap_and_scope(self):
        m,b,_=self.pack()
        mutations=[lambda x:x['parts'].reverse(),lambda x:x['parts'][1].update(object_id=x['parts'][0]['object_id']),lambda x:x['parts'][1].update(start_sample=44101),lambda x:x['parts'][1].update(start_sample=44099),lambda x:x['parts'][1].update(pack_offset=x['parts'][1]['pack_offset']+1),lambda x:x['parts'][1].update(pack_offset=x['parts'][1]['pack_offset']-1),lambda x:x['parts'][1].update(part_index=0),lambda x:x['parts'][1].update(pack_id='b'*32),lambda x:x.update(total_samples=88201),lambda x:x.update(pack_bytes=x['pack_bytes']+1),lambda x:x.update(lesson_id='TB-002'),lambda x:x.update(namespace='integrated-lectures/v1/directive')]
        for mutate in mutations:
            x=copy.deepcopy(m);mutate(x);self.rejects(p.validate_pack,x)
    def test_exact_part_bounds(self):
        m,b=self.part(b'X'*p.MAX_PART_BYTES,end=p.MAX_PART_SAMPLES);p.validate_part(m,b)
        self.assertEqual(self.open(m,b),b'X'*p.MAX_PART_BYTES)
        self.rejects(self.part,b'X'*(p.MAX_PART_BYTES+1))
        self.rejects(self.part,b'')
        for args in [{'end':0},{'end':p.MAX_PART_SAMPLES+1},{'start':-1},{'start':True},{'index':True},{'offset':True},{'offset':p.MAX_PACK_BYTES}]:self.rejects(self.part,**args)
        for field,value in [('kind','audio'),('version','old/v1'),('key_id','other'),('sample_rate',48000),('sample_rate',True),('chunk_bytes',True),('plain_bytes',True),('cipher_bytes',True)]:
            x=dict(m);x[field]=value;self.rejects(p.validate_part,x)
    def test_exact_pack_cap_without_allocating_large_audio(self):
        base,_=self.part();parts=[];offset=0
        # 63 maximum parts, then an exact-size last part reach 128 MiB including tags.
        for i in range(64):
            cipher_bytes=p.MAX_PART_BYTES+512 if i<63 else p.MAX_PACK_BYTES-offset
            plain_bytes=cipher_bytes-512;count=(plain_bytes+p.CHUNK-1)//p.CHUNK
            self.assertEqual(count,32)
            x=dict(base,object_id=f'{i+1:032x}',part_index=i,start_sample=i*44100,end_sample=(i+1)*44100,pack_offset=offset,plain_bytes=plain_bytes,cipher_bytes=cipher_bytes,cipher_sha256=['0'*64]*count)
            parts.append(x);offset+=cipher_bytes
        m={'version':p.PACK_VERSION,'kind':'audio-pack','key_id':p.KEY_ID,'namespace':'integrated-lectures/v1/tongbang','lesson_id':'TB-001','object_id':self.pid,'sample_rate':44100,'total_samples':64*44100,'pack_bytes':offset,'pack_sha256':'0'*64,'parts':parts}
        self.assertEqual(offset,134217728);p.validate_pack(m)
        x=copy.deepcopy(m);x['pack_bytes']+=1;self.rejects(p.validate_pack,x)
        x=copy.deepcopy(m);x['parts'][-1]['pack_offset']+=1;self.rejects(p.validate_pack,x)
    def test_part_cipher_swap_and_key_domain(self):
        a,x=self.part();b,y=self.part()
        self.assertNotEqual(a['object_id'],b['object_id'])
        altered=copy.deepcopy(a);altered['cipher_sha256']=b['cipher_sha256'];self.rejects(self.open,altered,y)
        self.rejects(p.seal_part,self.key,b'x','TB-026',self.pid,0,0,44100,0)
        self.rejects(p.seal_part,self.key,b'x','TB-001',self.pid,0,0,44100,0,key_id='other')
    def test_make_manifest_does_not_alias_input_parts(self):
        a,x=self.part();m=p.make_pack_manifest('TB-001',self.pid,[a],x);a['cipher_sha256'][0]='0'*64
        p.validate_pack(m,x)
    def test_canonical_base64_and_nonfinite_integers(self):
        m,b=self.part()
        for field,value in [('salt_b64',m['salt_b64']+'\n'),('nonce_prefix_b64','not-base64'),('start_sample',1.0),('end_sample',float('inf')),('part_index',-1),('part_index',2**32)]:
            x=dict(m);x[field]=value;self.rejects(p.validate_part,x)

if __name__=='__main__':unittest.main(verbosity=2)
