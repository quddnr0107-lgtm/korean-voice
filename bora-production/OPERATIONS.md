# Selected option 3: course production and rollout

The owner authorized production of all 303 lessons and deployment after each
complete course on 2026-11-12. Order: TB (25), RL (34), DR (78), MS (166).
Keep the existing audio and releases. Do not buy additional compute or switch
to a different voice, quantization or engine to improve throughput.

## First launch record

Run `43211498455`, commit `8dfa45d31686ea46462ac8e01e83d45d41f1009b`,
started 2026-11-12 11:42 UTC. Real keyless FP32 TTS/ASR/MP3 preflight passed,
and the authenticated TB plan passed: 25 lessons, 95 shards, 3665 units.
The initial plan found 704 source-review units, all caused by middle-dot legal
enumerations missing from the finite pronunciation dictionary. The first run
was cancelled early to avoid generating an entire course against that incomplete
dictionary. A reviewed dictionary of 243 exact whole lexemes now maps their
separators to spoken pauses without changing any lexical content or raw source.
Applying it to all 25 repository manuscripts resolves all 704 cases (1143 exact
substitutions); unknown notation still blocks. The protected operating-source
plan must independently reproduce zero review units before any new render matrix
starts. The new request is `tb-reviewed-enumerations-20261112`; read its latest
run rather than resuming cancelled run 43211498455. Option 3 model, voice, dtype,
threads, sampling and DSP settings remain unchanged. Current conservative reuse
binds the full plan and render fingerprint, so old incompatible checkpoints are
preserved but not mixed with the corrected plan.

Corrected run `43213606878` uses commit
`85d78a4cb034a53c9cac3dc651a240f14586bb78`. Its real preflight and protected
operating-source plan both passed; the latter authenticated all 25 lessons,
95 shards and 3665 units with zero source-review units. Its first 40 render jobs
were confirmed in progress with zero pending deployments at this handoff.
This is a launch observation, not a completion claim.

The first 40 corrected render jobs were approved through the existing environment UI.
Each later render wave and assembly can request that environment's approval
again. GitHub's bulk UI approval temporarily returned a server timeout while
approving part of the wave; re-reading the run showed exactly which requests
remained, and approving only those completed the wave. Verify actual pending
deployment state; do not infer failure from a timeout or disable the gate.

Site adapter PR #1012 merged at `d52c352446d05741f9c5f0dcb9852694a023417d`.
Worker deployment run `43212212246` succeeded, version
`dddea002-1bea-4f73-b2dd-3b41f9773092`. All old pins/catalog entries are retained;
no new course is activated. CI and deployed access-boundary checks passed.
Authenticated live lecture playback must still be checked when a course is
activated. Check current GitHub state rather than treating this launch record
as a current completion report.

## Entry point

Repository: `quddnr0107-lgtm/korean-voice`.
Approved branch: `integrated-lectures-premium-20261028`.
Workflow: `.github/workflows/bora-fp32-production.yml`.
Trigger: a new commit changing `bora-production/request.json` on that branch.
Read the current branch first; update it with an expected-head lease.
Do not change the old pilot request or reuse old BF16 recordings.

The request pins `campaign.json`, which pins the complete 303-source encrypted
inventory and `recipe.json`. The protected plan authenticates the exact operating
source before deriving source-preserving paragraph units. Public plans expose
only IDs, counts and hashes. No manuscript, ASR transcript, plaintext audio or
private requester recovery key belongs in this repository, logs or artifacts.

## Remaining-course source review

[remaining-source-review.json](remaining-source-review.json) records the RL, DR
and MS aggregate source-review counts, planner hashes and verified Git source
basis. It contains no manuscripts, notation examples, unit ranges or private
file paths. These repository-document diagnostics do not authenticate operating
payloads or approve audio: each subsequent protected plan must independently
pass the zero-source-review gate. They do not change the active TB request or
its production settings.

## Execution and gates

1. A keyless real FP32 public sentence test runs TTS, ASR, DSP and MP3 encoding.
2. A protected offline plan job authenticates sources and emits a bounded matrix.
3. At most 40 workers run. Each loads models once, then synthesizes units
   sequentially. Accepted units use exact canonical ASR, EOS and signal gates.
   Known source-review flags stay blocked. A green render job only means the
   encrypted checkpoint was saved; inspect passed/blocked/pending counts.
4. Each completed unit is immediately encrypted using the existing media-key
   checkpoint domain. Shard bundles are uploaded to the separate staging release.
   The renderer has a 270-minute soft budget and a 20-minute unit limit.
5. Assembly authenticates every checkpoint and requires every unit to pass.
   Timings come from actual decoded PCM, preserving all original paragraph and
   UTF-16/CP ranges. MP3s retain the existing independent-part encrypted-v2 format.
6. Only an entire verified course gets a public index. Upload the index last.
   No human listening approval is claimed by an automated test.

Staging tags: `bora-fp32-{tb,rl,dr,ms}-staging-20261112`.
Final tags: `bora-fp32-{tb,rl,dr,ms}-v1-20261112`.
These tags are distinct from the old three production releases.

## 2026-11-12 failed-render transport incident (needs operator intervention)

Corrected TB run 43213606878 is **not healthy** despite its successful protected
source plan: three first-wave render jobs completed synthesis and generated
public shard reports, then failed in the keyless ciphertext upload verification
with `asset_url`. GitHub's draft staging release advertises
`browser_download_url` paths under its temporary `untagged-<hex>` slug,
while the initial transport assumed the requested staging tag. One ciphertext
checkpoint bundle is present in draft staging; do not treat other job-local
checkpoints as persisted. Visible shard reports have blocked ASR/QA units;
zero-input-review does not imply zero-output-QA failures.

Fix committed as `18befe9938defc1947ac687aa2751fafbce8a392`:
`transport.py` now validates the draft temporary download slug against the
canonical release `html_url` and preserves strict published-tag URL checks.
Regression tests cover matching draft, mismatch, malicious host, published
mismatch, and idempotent re-download. Existing running jobs are pinned to the
older checkout and **cannot** receive this fix retroactively. Do not approve
their queued waves to spend more CPU on the known-broken transport. At last
read, three first-wave failures, 37 in progress, 52 queued and three waiting
for existing environment approval. No final TB index or site activation.

**Recovery:** cancel the failed 43213606878 run through the authorized GitHub
Actions UI, then trigger one new request.json `request_id` on the repaired
branch after re-checking HEAD and use the existing encrypted checkpoint in
staging. Keep `retry_blocked=false`. Before broad production, inspect persisted
public shard reports and safely diagnose *why* ASR/EOS gates block units,
without disclosing manuscript/audio or relaxing QA. Do not rerun cancelled
43211498455. No environment rules or model settings were changed.

## Resumption

Check workflow jobs and public shard reports, not only the overall conclusion.
Existing matching passed checkpoints are authenticated and reused; incompatible
source/recipe/render fingerprints cannot be reused. `retry_blocked=false` avoids
spending CPU on the same two deterministic failed candidates again. Resolve a
real cause or independently verify a recognition discrepancy before retrying;
never relabel a nonzero ASR mismatch, missing word, natural-EOS failure or source
review flag as passed just to unblock publication.

For a retry, change `request_id` and optionally `selected_shards` to a list of
`{"lesson_id":"TB-001","shard":0}` objects. The full lesson still needs all its
units for assembly. The request may include `lesson_ids` for a large-course batch;
the GitHub matrix must be <=256 jobs. MS requires batches. Partial final releases
may hold verified encrypted lesson triples, but cannot contain a course index or
activate a partial course. Reassemble only missing lessons to avoid conflicting
immutable random object IDs. No `--clobber` or old-asset deletion.

The setup/model steps are keyless. Source/media secrets appear only in the
existing `admin-law-premium-production` environment's offline unprivileged
network namespace. Keep protection rules and approval gates intact. Authorized
approval may be exercised normally; do not remove the gate to get a run through.

## Site activation after a course completes

Repository: `quddnr0107-lgtm/yebijun-muhanhoidok234`.
The deployed course-release adapter registers old and new release pairs, leaving
new releases disabled until their explicit `BORA_TB_INDEX_SHA256` (or RL/DR/MS)
pin is configured. Its source is merged into main through PR #1012.
Follow `docs/BORA_COURSE_RELEASES_20261112.md` there.

Verify the new entire-course index and all referenced encrypted objects. Deploy
the Worker with the added course pin while preserving all old pins. Then update
only that course's catalog versions and measured durations using the dedicated
catalog updater, preserving all other lesson rows. Wait for exact-head CI and
Worker/Pages deployment success. Verify real permitted playback, paragraph
highlighting, seeking/resume, daily-free/premium access and print behavior.
Only then report that course as deployed and start the next course.

Use an hourly conditional continuation task while the campaign runs. Notify the
owner when a course is actually deployed or a concrete blocker requires them;
do not send repetitive progress notices. Stop the continuation when all four
courses are deployed. Preserve old assets until separate explicit deletion.
