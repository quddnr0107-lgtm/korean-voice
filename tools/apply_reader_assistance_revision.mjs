import fs from 'node:fs';
import crypto from 'node:crypto';

const digest = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const blob = bytes => crypto.createHash('sha1').update(Buffer.from('blob '+bytes.length+'\0')).update(bytes).digest('hex');
const requireValue = (ok, why) => { if (!ok) throw new Error(why); };
const patchPath = 'reader-assistance/transfer/revision-21db3f3e.json';
const patch = JSON.parse(fs.readFileSync(patchPath, 'utf8'));
requireValue(patch.schema === 'reader-assistance-exact-revision.v1', 'Wrong revision schema');
requireValue(patch.baseSha256 === '39df5c0dabc5a2c96b1b8a9d6bb9d485c51ed3f8078d8287a9667258466596f7', 'Unexpected old text');
requireValue(patch.targetSha256 === '21db3f3e2f8e5b9584bea2cc887175e78e542d33fd9e8a060f6c8ed9943f32af', 'Unexpected corrected text');
const headers = {Authorization: 'Bearer '+process.env.GH_TOKEN, Accept: 'application/vnd.github+json', 'Content-Type': 'application/json'};
const api = 'https://api.github.com/repos/quddnr0107-lgtm/korean-voice/git/blobs';
let oldBytes;
if (process.argv[2]) oldBytes = fs.readFileSync(process.argv[2]);
else {
  const response = await fetch(api+'/'+patch.baseGitBlob, {headers, signal: AbortSignal.timeout(60000)});
  requireValue(response.ok, 'Read old immutable blob: '+response.status);
  const result = await response.json();
  requireValue(result.encoding === 'base64' && result.sha === patch.baseGitBlob, 'Wrong source blob');
  oldBytes = Buffer.from(result.content, 'base64');
}
requireValue(digest(oldBytes) === patch.baseSha256 && blob(oldBytes) === patch.baseGitBlob, 'Old canonical bytes differ');
const data = JSON.parse(oldBytes);
const changed = new Set();
const groups = new Set(), metadata = new Set();
for (const op of patch.operations) {
  if (op.path.length === 1 && ['version','sourceBasis'].includes(op.path[0])) {
    const key = op.path[0];
    requireValue(!metadata.has(key), 'Duplicate revision metadata');
    if (key === 'sourceBasis') requireValue(data.sourceBasis.sourceDataSha256 === op.value.sourceDataSha256 && data.sourceBasis.corpusGitBlob === op.value.corpusGitBlob, 'Original corpus changed');
    metadata.add(key); data[key] = op.value; continue;
  }
  if (op.path.length === 2 && op.path[0] === 'tableGroups' && Number.isSafeInteger(op.path[1])) {
    const index = op.path[1], old = data.tableGroups[index];
    requireValue(old && !groups.has(index) && old.id === op.value.id && old.anchorSourceId === op.value.anchorSourceId && JSON.stringify(old.memberSourceIds) === JSON.stringify(op.value.memberSourceIds), 'Table group identity changed');
    groups.add(index); data.tableGroups[index] = op.value; continue;
  }
  requireValue(op.path.length === 2 && op.path[0] === 'entries' && Number.isSafeInteger(op.path[1]), 'Only approved entries may change');
  const index = op.path[1];
  requireValue(index >= 0 && index < data.entries.length && !changed.has(index), 'Invalid or duplicate entry');
  changed.add(index);
  requireValue(data.entries[index].id === op.value.id && data.entries[index].original === op.value.original && data.entries[index].sourceIndex === op.value.sourceIndex, 'Original identity changed');
  data.entries[index] = op.value;
}
requireValue(changed.size === 32 && groups.size === 9 && metadata.size === 2, 'Unexpected revision scope');
const bytes = Buffer.from(JSON.stringify(data, null, 2)+'\n');
requireValue(bytes.length === patch.targetBytes && digest(bytes) === patch.targetSha256 && blob(bytes) === patch.targetGitBlob, 'Corrected canonical bytes differ');
requireValue(data.partial === true && data.entries.length === 6072 && data.exclusions.length === 1 && data.exclusions[0].sourceIndex === 6384 && data.exclusions[0].status === 'blocked', 'Approved scope changed');
if (!process.argv[2]) {
  const response = await fetch(api, {method: 'POST', headers, body: JSON.stringify({content: bytes.toString('base64'), encoding:'base64'}), signal:AbortSignal.timeout(120000)});
  requireValue(response.ok, 'Register corrected immutable blob: '+response.status);
  const result = await response.json();
  requireValue(result.sha === patch.targetGitBlob, 'Registered Git identity differs');
}
console.log(JSON.stringify({ok:true,localOnly:!!process.argv[2],changed:changed.size,sha:patch.targetGitBlob,sha256:patch.targetSha256,bytes:bytes.length}));
