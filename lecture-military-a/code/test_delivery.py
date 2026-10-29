"""Mock GH only. No remote writes, real tokens, or secret reads."""
import copy,io,json,os,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch,Mock
import lecture_crypto as c
import delivery as d
import lecture_pack as pack

class Remote:
    def __init__(self,objects=None):self.objects=dict(objects or {});self.uploaded=[]
    @property
    def assets(self):return {n:{'name':n,'size':len(b),'digest':'sha256:'+d.sha(b)} for n,b in self.objects.items()}
    def download(self,names,out):
        out=Path(out);out.mkdir(parents=True,exist_ok=True)
        if any(out.iterdir()):raise ValueError('nonempty')
        for n in names:(out/n).write_bytes(self.objects[n])
    def upload(self,p):
        if p.name in self.objects:raise ValueError('overwrite')
        self.objects[p.name]=p.read_bytes();self.uploaded.append(p.name)

class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.master=b'T'*32
    def complete(self,ident='MS-001',name='out'):
        out=self.root/name;out.mkdir();desc={'version':'integrated-lecture-output/v2','lesson_id':ident}
        # Every attempt receives a fresh pack ID, not just fresh part IDs.
        import secrets
        pid=secrets.token_hex(16);part,cipher=pack.seal_part(self.master,b'synthetic-audio-part',ident,pid,0,0,44100,0);audio=pack.make_pack_manifest(ident,pid,[part],cipher)
        desc['audio']=audio;(out/d.media_name(ident,'audio',audio)).write_bytes(cipher)
        meta,blob=c.seal(self.master,b'synthetic-private-metadata',ident,'private-metadata',d.KEY_ID)
        desc['content']=meta;(out/d.media_name(ident,'content',meta)).write_bytes(blob)
        (out/d.descriptor_name(ident)).write_bytes(d.encoded(desc));return out
    def checkpoint(self,ident='MS-001',run=99,attempt=1,name='cp'):
        out=self.root/name;out.mkdir();stem=f'{ident}.checkpoint.{run}.{attempt}';m,b=c.seal(self.master,b'fake private zip',ident,'checkpoint',d.KEY_ID)
        (out/(stem+'.bin')).write_bytes(b);(out/(stem+'.json')).write_bytes(d.encoded(m));return out
    def audio_name(self,out):return next(out.glob('*.audio.*.bin')).name
    def objects(self,p):return {f.name:f.read_bytes() for f in p.iterdir()}
    def rejects(self,fn,*a,**kw):
        with self.assertRaises((ValueError,KeyError)):fn(*a,**kw)
    def test_queue_exact83(self):
        q={'version':'integrated-lecture-queue/v1','count':83,'lessons':[{'id':i,'file':i+'.sealed.json','cipher_sha256':'a'*64} for i in sorted(d.IDS)]}
        p=self.root/'queue.json';p.write_bytes(d.encoded(q));self.assertEqual(len(d.validate_queue(self.root)['lessons']),83)
        for mutate in [lambda x:x['lessons'].pop(),lambda x:x['lessons'].__setitem__(0,x['lessons'][1]),lambda x:x.update(count=True),lambda x:x['lessons'][0].update(file='../x')]:
            bad=copy.deepcopy(q);mutate(bad);p.write_bytes(d.encoded(bad));self.rejects(d.validate_queue,self.root)
    def test_output_gates(self):
        out=self.complete();self.assertEqual(d.output_gate(out,'MS-001')[0],'complete')
        for suffix in ['mp3','wav','flac','zip','txt']:
            p=out/('leak.'+suffix);p.write_bytes(b'private');self.rejects(d.output_gate,out,'MS-001');p.unlink()
        target=out/'link';target.symlink_to(out/self.audio_name(out));self.rejects(d.output_gate,out,'MS-001');target.unlink()
        desc=out/'MS-001.descriptor.json';v=d.loads(desc.read_bytes());v['plaintext']='leak';desc.write_bytes(d.encoded(v));self.rejects(d.output_gate,out,'MS-001')
    def test_scope_and_cipher_hash(self):
        out=self.complete();self.rejects(d.output_gate,out,'MS-002')
        p=out/self.audio_name(out);b=p.read_bytes();p.write_bytes(bytes([b[0]^1])+b[1:]);self.rejects(d.output_gate,out,'MS-001')
    def test_checkpoint_gate(self):
        out=self.checkpoint();self.assertEqual(d.output_gate(out,'MS-001',run_id='99',attempt='1')[0],'checkpoint')
        self.rejects(d.output_gate,out,'MS-001',run_id='98');self.rejects(d.output_gate,out,'MS-001',attempt='2')
        m=next(out.glob('*.json'));v=d.loads(m.read_bytes());v['kind']='audio';m.write_bytes(d.encoded(v));self.rejects(d.output_gate,out,'MS-001')
    def test_publish_reuse_conflict_and_orphans(self):
        out=self.complete();objs=self.objects(out);an=self.audio_name(out)
        for pre in [{},{an:objs[an]},objs]:
            remote=Remote(pre)
            with patch.object(d,'Release',return_value=remote):result=d.publish(remote,'MS-001',out)
            self.assertEqual(remote.objects,objs)
            if remote.uploaded and 'MS-001.descriptor.json' not in pre:self.assertEqual(remote.uploaded[-1],'MS-001.descriptor.json')
            if pre==objs:self.assertEqual(result['status'],'reused');self.assertEqual(remote.uploaded,[])
        remote=Remote({an:b'conflicting'})
        with patch.object(d,'Release',return_value=remote):self.rejects(d.publish,remote,'MS-001',out)
        self.assertEqual(remote.uploaded,[])
    def test_old_orphan_different_object_id_does_not_block_new_commit(self):
        old=self.complete(name='old');new=self.complete(name='new');old_objects=self.objects(old);old_objects.pop('MS-001.descriptor.json')
        remote=Remote(old_objects)
        # No descriptor means no completed lesson, regardless of orphan binaries.
        self.assertEqual(d.fetch(remote,'MS-001',self.root/'before-fetch')['kind'],'none')
        with patch.object(d,'Release',return_value=remote):self.assertEqual(d.publish(remote,'MS-001',new)['status'],'published')
        for n,b in old_objects.items():self.assertEqual(remote.objects[n],b)
        self.assertEqual(remote.objects['MS-001.descriptor.json'],(new/'MS-001.descriptor.json').read_bytes())
        dest=self.root/'new-fetch';self.assertEqual(d.fetch(remote,'MS-001',dest)['kind'],'complete')
        self.assertEqual(self.objects(dest),self.objects(new))
        # A later random re-encryption may not replace the committed descriptor.
        uploaded=list(remote.uploaded)
        with patch.object(d,'Release',return_value=remote):self.rejects(d.publish,remote,'MS-001',old)
        self.assertEqual(remote.uploaded,uploaded)
    def test_descriptor_missing_referenced_blob_fails_closed(self):
        out=self.complete();obj=self.objects(out);desc=obj['MS-001.descriptor.json']
        remote=Remote({'MS-001.descriptor.json':desc})
        self.rejects(d.fetch,remote,'MS-001',self.root/'partial-fetch')
        with patch.object(d,'Release',return_value=remote):self.rejects(d.publish,remote,'MS-001',out)
        self.assertEqual(remote.uploaded,[])
        # Unrelated orphan media cannot satisfy a committed descriptor's reference.
        other=self.complete(name='other');remote.objects.update({n:b for n,b in self.objects(other).items() if n.endswith('.bin')})
        self.rejects(d.fetch,remote,'MS-001',self.root/'wrong-reference')
    def test_binary_verification_precedes_descriptor_commit(self):
        out=self.complete();remote=Remote()
        real=remote.upload
        def corrupt(path):
            real(path)
            if path.name.endswith('.audio.bin'):raise AssertionError('fixed names forbidden')
            if '.audio.' in path.name:remote.objects[path.name]=b'corrupted upload'
        with patch.object(remote,'upload',side_effect=corrupt),patch.object(d,'Release',return_value=remote):self.rejects(d.publish,remote,'MS-001',out)
        self.assertNotIn('MS-001.descriptor.json',remote.objects)
    def test_output_descriptor_object_names_are_exact(self):
        out=self.complete();p=out/self.audio_name(out);p.rename(out/'MS-001.audio.bin')
        self.rejects(d.output_gate,out,'MS-001')
    def test_empty_publish_only_verified_completed_remote(self):
        empty=self.root/'empty-output';empty.mkdir()
        self.rejects(d.publish,Remote(),'MS-001',empty)
        out=self.complete();remote=Remote(self.objects(out))
        result=d.publish(remote,'MS-001',empty)
        self.assertEqual(result['status'],'already_complete');self.assertEqual(remote.uploaded,[])
        remote.objects[self.audio_name(out)]=b'tampered'
        self.rejects(d.publish,remote,'MS-001',empty)
        linked=self.root/'linked';linked.symlink_to(empty,target_is_directory=True)
        self.rejects(d.publish,remote,'MS-001',linked)
    def test_fetch_complete_or_latest_checkpoint(self):
        out=self.complete();cp=self.checkpoint(run=99);cp2=self.checkpoint(run=100,attempt=2,name='cp2');objects={**self.objects(cp),**self.objects(cp2)}
        remote=Remote(objects);r=d.fetch(remote,'MS-001',self.root/'fetch1');self.assertEqual(r['stem'],'MS-001.checkpoint.100.2')
        remote.objects.update(self.objects(out));r=d.fetch(remote,'MS-001',self.root/'fetch2');self.assertEqual(r['kind'],'complete')
        with patch.object(d,'Release',return_value=remote):r=d.publish(remote,'MS-001',cp)
        self.assertEqual(r['status'],'checkpoint-not-needed');self.assertEqual(remote.uploaded,[])
    def test_fetch_none_and_committed_incomplete(self):
        r=d.fetch(Remote(),'MS-034',self.root/'empty');self.assertEqual(r['kind'],'none')
        self.rejects(d.fetch,Remote({'MS-001.descriptor.json':b'{}'}),'MS-001',self.root/'bad')
    def test_asset_reservation_boundary_and_overrun_no_writes(self):
        old=self.complete(name='old-cap');new=self.complete(name='new-cap');cp=self.checkpoint(name='cap-checkpoint')
        oldbins={n:b for n,b in self.objects(old).items() if n.endswith('.bin')}
        initial={**oldbins,**self.objects(cp)}
        remote=Remote(initial)
        self.assertEqual(len(remote.assets),4)
        with patch.object(d,'Release',return_value=remote):self.assertEqual(d.publish(remote,'MS-001',new)['status'],'published')
        self.assertEqual(len(remote.assets),7)
        extra='MS-001.audio.'+'f'*32+'.bin';remote=Remote({**initial,extra:b'old-orphan'})
        with patch.object(d,'Release',return_value=remote):self.rejects(d.publish,remote,'MS-001',new)
        self.assertEqual(remote.uploaded,[])
    def test_global_capacity_582_and_exact_audio_cap(self):
        inventory=set()
        for ident in d.IDS:
            inventory.add(d.descriptor_name(ident))
            for i in range(6):inventory.add(ident+'.audio.'+f'{i:032x}'+'.bin')
        self.assertEqual(len(inventory),581);d.validate_asset_capacity(inventory,{'index.json'})
        self.rejects(d.validate_asset_capacity,inventory,{'MS-001.content.'+'f'*32+'.bin','index.json'})
        self.assertEqual(d.cap_for('MS-001.audio.'+'a'*32+'.bin'),134217728)
        self.assertEqual(d.cap_for('MS-001.content.'+'a'*32+'.bin'),8388608+16*128)
        self.assertEqual(d.cap_for('index.json'),131072)
        self.assertEqual(d.cap_for('MS-001.descriptor.json'),524288)
        self.assertEqual(d.TAG,'military-service-a-premium-v2-20261029')
    def test_prepare_gh_scope_mock(self):
        responses=[[[]],{'tag_name':d.TAG,'draft':False,'id':555},[[]]]
        def gh(*args,**kw):
            if args[:2]==('release','create'):return None
            return responses.pop(0)
        with patch.object(d,'gh',side_effect=gh) as mock,patch.dict(os.environ,{'GITHUB_SHA':'a'*40}):d.Release(prepare=True)
        calls=[x.args for x in mock.call_args_list];self.assertTrue(any(x[:3]==('release','create',d.TAG) for x in calls))
        self.assertTrue(all('admin-law' not in ' '.join(x) for x in calls))
    def test_remote_scope_and_no_premature_index(self):
        base={'tag_name':d.TAG,'draft':False,'id':1}
        for asset in [{'name':'leak.mp3','size':5},{'name':'AL-011.audio.bin','size':5},{'name':'MS-001.audio.bin','size':5},{'name':'index.json','size':5}]:
            with patch.object(d,'gh',side_effect=[base,[[asset]]]):self.rejects(d.Release)
    def test_gh_forwards_token_not_source_secrets(self):
        response=Mock(stdout=b'{}')
        with patch.dict(os.environ,{'GH_TOKEN':'synthetic-token','SOURCE_KEY':'must-not-forward'}),patch.object(d.subprocess,'run',return_value=response) as run:
            self.assertEqual(d.gh('api','example',json_output=True),{})
        env=run.call_args.kwargs['env'];self.assertEqual(env['GH_TOKEN'],'synthetic-token');self.assertNotIn('SOURCE_KEY',env)
    def write_queue(self):
        q={'version':'integrated-lecture-queue/v1','count':83,'lessons':[{'id':i,'file':i+'.sealed.json','cipher_sha256':'a'*64} for i in sorted(d.IDS)]}
        path=self.root/'queue.json';path.write_bytes(d.encoded(q));return path
    def test_finalize_waits_for_all83_and_is_immutable(self):
        queue=self.write_queue();remote=Remote()
        result=d.finalize(remote,queue);self.assertEqual(result['status'],'incomplete');self.assertEqual(remote.uploaded,[])
        for ident in sorted(d.IDS):remote.objects.update(self.objects(self.complete(ident,name=ident)))
        raw_name=d.descriptor_name(sorted(d.IDS)[0]);remote.objects[raw_name]=b'  '+remote.objects[raw_name]+b'\n'
        # 83 descriptors alone do not authorize an index: every referenced
        # ciphertext must exist and validate, even if unrelated orphans exist.
        first=sorted(d.IDS)[0];missing=self.audio_name(self.root/first);saved=remote.objects.pop(missing)
        with patch.object(d,'Release',return_value=remote):self.rejects(d.finalize,remote,queue)
        self.assertNotIn('index.json',remote.objects);remote.objects[missing]=saved
        with patch.object(d,'Release',return_value=remote):
            self.assertEqual(d.finalize(remote,queue)['status'],'published')
            self.assertEqual(d.finalize(remote,queue)['status'],'reused')
        index=d.loads(remote.objects['index.json']);self.assertEqual(index['count'],83);self.assertEqual(index['version'],'integrated-lecture-index/v2')
        self.assertEqual({x['id'] for x in index['lessons']},d.IDS)
        self.assertEqual(remote.uploaded,['index.json'])
        self.assertEqual(set(index),{'version','release_tag','count','lessons'})
        self.assertLessEqual(len(remote.objects['index.json']),131072)
        for row in index['lessons']:
            self.assertEqual(set(row),{'id','descriptor_url','sha256'})
            expected=f'https://github.com/{d.REPO}/releases/download/{d.TAG}/{row["id"]}.descriptor.json'
            self.assertEqual(row['descriptor_url'],expected)
            self.assertEqual(row['sha256'],d.sha(remote.objects[d.descriptor_name(row['id'])]))
        remote.objects['index.json']=b'{}'
        with patch.object(d,'Release',return_value=remote):self.rejects(d.finalize,remote,queue)
    def test_descriptor_size_cap_and_raw_bytes_hash(self):
        out=self.complete();path=out/'MS-001.descriptor.json';raw=path.read_bytes()
        # Arbitrary permissible JSON whitespace must be included in the root hash,
        # not normalized away by parse/re-serialization.
        path.write_bytes(b'  '+raw+b'\n');d.output_gate(out,'MS-001')
        self.assertNotEqual(d.sha(path.read_bytes()),d.sha(raw))
        path.write_bytes(b' '*(d.MAX_DESCRIPTOR+1-len(raw))+raw)
        self.rejects(d.output_gate,out,'MS-001')
        self.assertEqual(d.MAX_INDEX,128*1024);self.assertEqual(d.MAX_DESCRIPTOR,512*1024)
    def test_prepare_cli_matrix_output(self):
        queue=self.write_queue();out=self.root/'workflow-output'
        with patch('sys.argv',['delivery.py','prepare','--queue',str(queue)]),patch.dict(os.environ,{'GITHUB_REPOSITORY':d.REPO,'GITHUB_OUTPUT':str(out)}),patch.object(d,'Release'):
            self.assertEqual(d.main(),0)
        rows=dict(x.split('=',1) for x in out.read_text().splitlines())
        self.assertEqual(len(json.loads(rows['matrix'])['include']),83)
        self.assertEqual(rows['queue_sha256'],d.sha(queue.read_bytes()))
    def test_receiver_exact_zip_checks_mock(self):
        data={'receiver-public.json':d.encoded(c.PINNED_PUBLIC),'receiver-wrapped.json':b'{"synthetic":"not a real wrapper"}'}
        buff=io.BytesIO()
        with zipfile.ZipFile(buff,'w',zipfile.ZIP_DEFLATED) as z:
            for n,b in data.items():z.writestr(n,b)
        raw=buff.getvalue()
        with patch.object(d,'ARTIFACT_SHA',d.sha(raw)),patch.object(d,'RECEIVER_HASHES',{n:d.sha(b) for n,b in data.items()}),patch.object(d,'gh',return_value=raw) as mock:
            self.assertEqual(d.fetch_receiver(self.root/'receiver')['status'],'receiver-verified')
            self.assertIn(str(d.ARTIFACT_ID),mock.call_args.args[1])
        with patch.object(d,'gh',return_value=b'x'*2049):self.rejects(d.fetch_receiver,self.root/'too-large')

if __name__=='__main__':unittest.main(verbosity=2)
