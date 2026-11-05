import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import fs from 'node:fs';
import policy from '../reader-assistance/reviewed-subset-policy.json' with { type: 'json' };
import {handleReaderAssistanceMedia as handle, validateAssistanceManifest, validateReviewedSubsetLedger} from '../lib/reader-assistance-media.mjs';

const source = JSON.parse(fs.readFileSync(new URL('../reader-assistance/audio-manifest.json', import.meta.url), 'utf8'));
const sha = value => createHash('sha256').update(value).digest('hex');
const key = 'a'.repeat(64), pack = 'b'.repeat(64), bytes = new Uint8Array(800);
const address = 'https://korean-voice.quddnr0107.workers.dev/reader-assistance/f1/v1/' + key + '.mp3';
const dependencies = {cache: null, fetchImpl: () => {throw Error('No upstream access is allowed in this test');}};
function fixture() {
  return {schema:'reader-assistance-reviewed-subset-audio.v1', complete:false, reviewedScopeReady:true,
    releaseScope:structuredClone(policy), voice:'f1', recipe:'reader-assistance-f1-v1',
    recipeSha256:'325696fa16298826933979aa72f34cebf1aca3c1e473bb5035cd10265097f0b8',
    sourceDataSha256:'d'.repeat(64), expectedEntries:policy.approvedEntries, readyEntries:policy.approvedEntries,
    packs:{[pack]:{url:'https://github.com/quddnr0107-lgtm/korean-voice/releases/download/reader-assistance-f1-v1-packs/'+pack+'.bin',size:1200,sha256:pack}},
    clips:{[key]:{pack,offset:0,length:bytes.length,sha256:sha(bytes),durationMs:3000,textSha256:'c'.repeat(64)}},
    entries:source.entries.map(entry=>({id:entry.id,audioKey:key,sourceIndex:entry.sourceIndex,originalSha256:entry.originalSha256}))};
}

test('the one pinned reviewed subset stays incomplete but all its approved clips can be served', async()=>{
  const data=fixture();
  assert.equal(policy.bodyRows,6073);assert.equal(policy.approvedEntries,6072);assert.equal(policy.excluded.length,1);
  assert.equal(policy.excluded[0].sourceIndex,6384);
  assert.equal(validateAssistanceManifest(data),data);
  assert.equal(await validateReviewedSubsetLedger(data),data);
  const response=await handle(new Request(address,{method:'HEAD'}),{},data,dependencies);
  assert.equal(response.status,200);assert.equal(response.headers.get('Content-Type'),'audio/mpeg');
  assert.equal(data.complete,false);assert.equal(data.entries.some(entry=>entry.id===policy.excluded[0].id),false);
  // JSON object order and entry order do not change the explicitly reviewed scope.
  data.releaseScope=Object.fromEntries(Object.entries(data.releaseScope).reverse());
  data.entries.reverse();
  assert.equal(validateAssistanceManifest(data),data);
  assert.equal(await validateReviewedSubsetLedger(data),data);
});

test('changing the exclusion, source scope, recipe, completeness, readiness or counts cannot activate audio', async()=>{
  const mutations=[m=>{m.complete=true;},m=>{m.reviewedScopeReady=false;},m=>{m.schema='reader-assistance-audio-pack.v1';},
    m=>{m.releaseScope.excluded[0].reason+=' altered';},m=>{m.releaseScope.excluded.push({...m.releaseScope.excluded[0]});},
    m=>{m.releaseScope.sourceCorpusSha256='0'.repeat(64);},m=>{m.releaseScope.sourceRowsSha256='0'.repeat(64);},
    m=>{m.recipeSha256='0'.repeat(64);},m=>{m.expectedEntries--;m.readyEntries--;m.entries.pop();},m=>{m.readyEntries--;}];
  for(const mutate of mutations){const data=fixture();mutate(data);assert.throws(()=>validateAssistanceManifest(data));
    const response=await handle(new Request(address,{method:'HEAD'}),{},data,dependencies);assert.equal(response.status,503);}
});

test('the held source row, duplicate indices and malformed original identities are refused before media access', async()=>{
  const mutations=[m=>{m.entries[0].id=policy.excluded[0].id;},m=>{m.entries[0].sourceIndex=6384;},
    m=>{m.entries[0].sourceIndex=m.entries[1].sourceIndex;},m=>{m.entries[0].originalSha256='';},
    m=>{m.entries[0].id='unbound-source';},m=>{delete m.entries[0].sourceIndex;}];
  for(const mutate of mutations){const data=fixture();mutate(data);assert.throws(()=>validateAssistanceManifest(data));
    const response=await handle(new Request(address,{method:'HEAD'}),{},data,dependencies);assert.equal(response.status,503);}
});

test('matching counts cannot hide a substituted source ID, original hash or source index', async()=>{
  const mutations=[m=>{m.entries[0].id=sha('unreviewed replacement');},m=>{m.entries[0].originalSha256=sha('changed source');},
    m=>{m.entries[0].sourceIndex=999999;}];
  for(const mutate of mutations){const data=fixture();mutate(data);validateAssistanceManifest(data);
    await assert.rejects(validateReviewedSubsetLedger(data),/subset_source_ledger_mismatch/);
    const response=await handle(new Request(address,{method:'HEAD'}),{},data,dependencies);assert.equal(response.status,503);}
});
