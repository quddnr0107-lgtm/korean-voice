import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import fs from 'node:fs';
import vm from 'node:vm';
import {handleReaderAssistanceMedia as handle, validateAssistanceManifest, clientByteRange} from '../lib/reader-assistance-media.mjs';

const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const bytes = Uint8Array.from({length:1200}, (_, i) => i % 251);
const key = 'a'.repeat(64), packKey = 'b'.repeat(64), textHash = 'c'.repeat(64);
const address = 'https://korean-voice.quddnr0107.workers.dev/reader-assistance/f1/v1/' + key + '.mp3';
const packURL = 'https://github.com/quddnr0107-lgtm/korean-voice/releases/download/reader-assistance-f1-v1-packs/' + packKey + '.bin';
function fixture() {
  return {schema:'reader-assistance-audio-pack.v1', complete:true, voice:'f1', recipe:'reader-assistance-f1-v1',
    sourceDataSha256:'d'.repeat(64), recipeSha256:'e'.repeat(64), expectedEntries:1, readyEntries:1,
    packs:{[packKey]:{url:packURL,size:3000,sha256:packKey}},
    clips:{[key]:{pack:packKey,offset:600,length:bytes.length,sha256:sha(bytes),durationMs:7000,textSha256:textHash}},
    entries:[{id:'source-1',audioKey:key}]};
}
function source({body=bytes,status=206,range='bytes 600-1799/3000',length=String(bytes.length),encoding}={}) {
  return new Response(body,{status,headers:{'Content-Range':range,...(length!==null?{'Content-Length':length}:{}),...(encoding?{'Content-Encoding':encoding}:{})}});
}
function memoryCache() {
  const map = new Map();
  return {match:async r=>map.get(r.url)?.clone(),put:async (r,v)=>{map.set(r.url,v.clone());},size:()=>map.size};
}

test('the F1 route fetches exactly its public MP3 byte range and caches the verified bytes', async () => {
  const cache=memoryCache(), calls=[];
  const fetchImpl=async (url,init)=>{calls.push({url,init}); return source();};
  const data=fixture();
  const first=await handle(new Request(address),{},data,{fetchImpl,cache});
  assert.equal(first.status,200); assert.deepEqual(new Uint8Array(await first.arrayBuffer()),bytes);
  assert.equal(first.headers.get('Access-Control-Allow-Origin'),'*'); assert.equal(first.headers.get('X-Reader-Assistance-Voice'),'f1');
  assert.equal(first.headers.get('Content-Type'),'audio/mpeg'); assert.equal(cache.size(),1);
  assert.equal(calls[0].url,packURL); assert.equal(calls[0].init.headers.Range,'bytes=600-1799');
  assert.equal(calls[0].init.redirect,'manual'); assert.equal(calls[0].init.headers.Authorization,undefined);
  const hit=await handle(new Request(address),{},data,{fetchImpl,cache});
  assert.deepEqual(new Uint8Array(await hit.arrayBuffer()),bytes); assert.equal(calls.length,1);
});

test('release redirect preserves the bounded range and accepts only the verified GitHub asset host', async () => {
  const calls=[], destination='https://release-assets.githubusercontent.com/github-production-release-asset/123/abc?sig=example';
  const response=await handle(new Request(address),{},fixture(),{cache:null,fetchImpl:async(url,init)=>{
    calls.push({url,init}); return calls.length===1?new Response(null,{status:302,headers:{Location:destination}}):source();
  }});
  assert.equal(response.status,200); assert.equal(calls.length,2); assert.equal(calls[1].url,destination);
  assert.equal(calls[1].init.headers.Range,'bytes=600-1799');
  for(const location of ['https://evil.example/data','http://release-assets.githubusercontent.com/github-production-release-asset/a','https://u:p@release-assets.githubusercontent.com/github-production-release-asset/a','https://release-assets.githubusercontent.com/other/path']) {
    let count=0;
    const bad=await handle(new Request(address),{},fixture(),{cache:null,fetchImpl:async()=>{count++;return new Response(null,{status:302,headers:{Location:location}});}});
    assert.equal(bad.status,502); assert.equal(count,1);
  }
});

test('range-ignored, wrong-span, truncated, oversized, encoded and corrupt upstream bytes never become audio',async()=>{
  const cases=[()=>source({status:200}),()=>source({range:'bytes 0-1199/3000'}),()=>source({range:'bytes 600-1799/3001'}),
    ()=>source({length:'1201'}),()=>source({body:bytes.slice(0,1100),length:null}),()=>source({body:new Uint8Array(1300),length:null}),
    ()=>source({body:new Uint8Array(1200)}),()=>source({encoding:'gzip'}),()=>new Response(null,{status:404})];
  for(const make of cases){const cache=memoryCache();const r=await handle(new Request(address),{},fixture(),{cache,fetchImpl:async()=>make()});
    assert.equal(r.status,502);assert.equal(cache.size(),0);assert.equal(r.headers.get('Cache-Control'),'no-store');}
});

test('partial manifests, mismatched counts, duplicate IDs and unsafe packs are refused without any request',async()=>{
  const cases=[m=>{m.complete=false;},m=>{m.readyEntries=0;},m=>{m.voice='female';},m=>{m.recipe='other';},m=>{m.sourceDataSha256='';},
    m=>{m.packs[packKey].url='https://evil.example/audio.bin';},m=>{m.clips[key].offset=2999;},m=>{m.clips[key].durationMs=NaN;},
    m=>{m.entries.push({...m.entries[0]});m.expectedEntries=m.readyEntries=2;},m=>{m.clips['f'.repeat(64)]={...m.clips[key]};}];
  for(const mutate of cases){const data=fixture();mutate(data);assert.throws(()=>validateAssistanceManifest(data));
    const r=await handle(new Request(address),{},data,{cache:null,fetchImpl:()=>{throw Error('must not fetch');}});assert.equal(r.status,503);}
});

test('unknown hashes, methods and malformed paths cannot trigger synthesis or network requests',async()=>{
  for(const [url,method,status] of [[address.replace(key,'0'.repeat(64)),'GET',404],[address,'POST',405],[address+'?url=https://evil.example','GET',404],
    [address.replace('f1','female'),'GET',404],[address.replace(key,'short'),'GET',404]]) {
    const r=await handle(new Request(url,{method}),{},fixture(),{cache:null,fetchImpl:()=>{throw Error('must not fetch');}});assert.equal(r.status,status);
  }
  const preflight=await handle(new Request(address,{method:'OPTIONS'}),{},null,{fetchImpl:()=>{throw Error('must not fetch');}});
  assert.equal(preflight.status,204);assert.match(preflight.headers.get('Access-Control-Allow-Headers'),/Range/);
});

test('HEAD, ETag and unsatisfiable client ranges are answered locally',async()=>{
  const deps={cache:null,fetchImpl:()=>{throw Error('must not fetch');}},data=fixture();
  const head=await handle(new Request(address,{method:'HEAD'}),{},data,deps);
  assert.equal(head.status,200);assert.equal(head.headers.get('Content-Length'),'1200');assert.equal(await head.text(),'');
  const rangedHead=await handle(new Request(address,{method:'HEAD',headers:{Range:'bytes=0-99'}}),{},data,deps);
  assert.equal(rangedHead.status,206);assert.equal(rangedHead.headers.get('Content-Range'),'bytes 0-99/1200');
  const etag='"'+sha(bytes)+'"';
  const unchanged=await handle(new Request(address,{headers:{'If-None-Match':etag}}),{},data,deps);assert.equal(unchanged.status,304);
  for(const range of ['bytes=1200-','bytes=10-9','bytes=0-1,3-4','bytes=-0','bytes=-','bytes=9007199254740993-']){
    const response=await handle(new Request(address,{headers:{Range:range}}),{},data,deps);
    assert.equal(response.status,416);assert.equal(response.headers.get('Content-Range'),'bytes */1200');
  }
});

test('client ranges are cut from the verified clip and never select another part of the pack',async()=>{
  const cache=memoryCache(),data=fixture();let calls=0;const deps={cache,fetchImpl:async()=>{calls++;return source();}};
  for(const [range,start,end] of [['bytes=0-99',0,99],['bytes=1150-',1150,1199],['bytes=-25',1175,1199],['bytes=100-9999',100,1199]]){
    const r=await handle(new Request(address,{headers:{Range:range}}),{},data,deps);
    assert.equal(r.status,206);assert.equal(r.headers.get('Content-Range'),`bytes ${start}-${end}/1200`);
    assert.deepEqual(new Uint8Array(await r.arrayBuffer()),bytes.slice(start,end+1));
  }
  assert.equal(calls,1);
  const full=await handle(new Request(address,{headers:{Range:'bytes=0-5','If-Range':'"old"'}}),{},data,deps);
  assert.equal(full.status,200);assert.equal((await full.arrayBuffer()).byteLength,1200);
  assert.deepEqual(clientByteRange('bytes=-9999',1200),{start:0,end:1199});
});

test('cache unavailability does not discard a valid F1 response',async()=>{
  for(const cache of [{match:()=>{throw Error('unavailable');},put:()=>{throw Error('quota');}}, {match:async()=>null,put:async()=>{throw Error('quota');}}]){
    const r=await handle(new Request(address),{},fixture(),{cache,fetchImpl:async()=>source()});assert.equal(r.status,200);
  }
});

test('a same-length corrupt cache entry cannot impersonate the sealed MP3 digest',async()=>{
  const data=fixture(), cache=memoryCache();
  await cache.put(new Request(address),new Response(new Uint8Array(bytes.length),{headers:{
    'Content-Length':String(bytes.length),'X-Reader-Assistance-SHA256':sha(bytes)
  }}));
  let fetched=0;
  const response=await handle(new Request(address),{},data,{cache,fetchImpl:async()=>{fetched++;return source();}});
  assert.equal(response.status,200);
  assert.deepEqual(new Uint8Array(await response.arrayBuffer()),bytes);
  assert.equal(fetched,1);
});

test('the real Worker router delegates F1 requests before touching any existing service bindings',async()=>{
  const src=fs.readFileSync(new URL('../worker.mjs',import.meta.url),'utf8');let serviceCalls=0,delegations=0;
  const trap=()=>{serviceCalls++;throw Error('service access');};
  const context=vm.createContext({URL,Request,Response,Container:class{},DurableObject:class{},assistanceAudioManifest:fixture(),
    handleReaderAssistanceMedia:async(request,ctx,manifest)=>{delegations++;assert.equal(request.url,address);assert.equal(manifest.voice,'f1');return new Response('ok');}});
  vm.runInContext(src.replace(/^import .*;\r?$/gm,'').replace(/^export \{.*;\r?$/gm,'').replace(/^export class /gm,'class ').replace('export default {','globalThis.worker = {'),context);
  const response=await context.worker.fetch(new Request(address),new Proxy({}, {get:trap}),{});
  assert.equal(response.status,200);assert.equal(delegations,1);assert.equal(serviceCalls,0);
});
