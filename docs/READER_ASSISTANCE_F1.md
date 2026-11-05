# Reviewed F1 assistance audio packs

The selected assistance voice is **F1**. Original-law reading keeps its existing F2 path. This pipeline does not add a cold F1 voice to the Container, change `/tts`, call R2, or invoke a paid TTS API.

## Input and approval gate

The public input is `reader-assistance/data.json`, using the site's `reader-assistance.v1` schema. Every entry must have a unique nonempty `id`, its exact reviewed `text`, and:

```json
{"review":{"status":"approved","textSha256":"SHA-256 of exact UTF-8 text"}}
```

Normal runs also require `partial: false`, `coverage.bodyRows === coverage.explainedRows === entries.length`, and `coverage.unexplainedBodyRows === 0`. The pipeline rejects missing, changed, duplicate, or unapproved entries before any synthesis. It does not create approval records or rewrite legal explanations.

`reader-assistance/pilot.json` contains the seven previously reviewed real paragraphs. `--pilot` permits their explicitly incomplete coverage and preserves `complete: false`. They use the production F1 recipe and pack namespace so their audio can be reused. The pilot catalog is published only to `data/reader-assistance-f1-v1-pilot`; it cannot activate the production Worker or attach to a complete source.

Synthetic test fixtures use a separate `-fixture-packs` namespace and cannot publish a catalog branch.

## Exact content identity

`recipe.json` pins F1 style, the chosen sample's settings, all seven model/style file SHA-256 values, the helper Git blob, and renderer source blobs. It uses the same recipe as sample run [41940068323](https://github.com/quddnr0107-lgtm/korean-voice/actions/runs/41940068323): neutral, rate 1.05, base speed 1, 16 steps, no owner profile, no pitch/EQ/compression changes, and peak normalization to -1 dBFS.

The Python and Node implementations share this identity contract and have a cross-language test:

```text
canonicalRecipe = JSON.stringify(recursively key-sorted recipe JSON)
textSha256 = SHA256(UTF8(entry.text))
audioKey = SHA256(UTF8(canonicalRecipe + "\n" + textSha256))
bucket = audioKey[0]
```

The exact source data file's SHA-256 is recorded independently. Whitespace or text changes invalidate its approval and audio binding. Source renderer changes require a reviewed recipe reseal; they are not silently accepted.

## Cost and storage

`korean-voice` is public. The workflow uses standard `ubuntu-latest` CPU runners, with at most sixteen synthesis jobs in parallel. GitHub's [Actions billing documentation](https://docs.github.com/en/actions/concepts/billing-and-usage) states that standard hosted runners are free for public repositories. Larger/GPU runners are not used.

One verified Supertonic model cache is shared across jobs. A loaded model and F1 style are reused inside each job; existing audio keys skip model synthesis. The same text under the same recipe is baked once even when multiple legal rows use it. No audio is committed to Git or Git LFS, and no Actions audio artifact is retained.

Each immutable pack holds at most 128 MP3 clips and at most 20 MiB. The MP3 bytes are concatenated unchanged. A compact JSON index and a fixed 48-byte footer describe exact byte offsets, lengths, clip SHA-256 values, text hashes, and durations. The footer includes its index hash. The asset filename is the whole pack's SHA-256 plus `.bin`.

Packs are published in the public `reader-assistance-f1-v1-packs` Release. GitHub's [Release documentation](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases) permits up to 1,000 assets per release, each under 2 GiB, and currently specifies no total release-size or bandwidth quota. Packing roughly 6,073 clips needs about 48–64 assets rather than thousands of per-clip API uploads. Actual count depends on hash buckets and the size cap.

The publisher never replaces or deletes an existing asset. It honors rate-limit headers and stops on ambiguous failures. Retrying reads the Release inventory and exact footer/index ranges to reuse previously published clips. At most the unfinished in-memory pack needs synthesis again after interruption.

## Audio and delivery checks

Before publishing, each clip must pass finite-sample, mono/44.1 kHz, text-based duration, non-silence, clipping, and silence-gap checks. The MP3 is decoded with FFmpeg, checked again, and compared with the source duration. Temporary WAV files are removed immediately. These objective checks do not claim to prove every pronunciation or subjective listening quality.

Every uploaded pack is checked against GitHub's reported SHA-256 and size. Its public download is probed with exact `Range` requests. Redirects may go only from the fixed public GitHub URL to `release-assets.githubusercontent.com`; HTTP 206, exact `Content-Range`, exact length, identity encoding, and index checksum must all match. A failure stops the job before further packs are published.

After all matrix jobs succeed, assembly requires every reviewed `audioKey`. Only then is a complete manifest atomically committed to the dedicated `data/reader-assistance-f1-v1` branch. `main`, the study-site repository, and production deployments are not changed by this workflow. The manifest has this shape:

```json
{
  "schema": "reader-assistance-audio-pack.v1",
  "complete": true,
  "voice": "f1",
  "recipe": "reader-assistance-f1-v1",
  "sourceDataSha256": "...",
  "recipeSha256": "...",
  "expectedEntries": 6073,
  "readyEntries": 6073,
  "packs": {"PACK_SHA256":{"url":"https://github.com/quddnr0107-lgtm/korean-voice/releases/download/reader-assistance-f1-v1-packs/PACK_SHA256.bin","size":12345,"sha256":"PACK_SHA256"}},
  "clips": {"AUDIO_KEY":{"pack":"PACK_SHA256","offset":0,"length":12000,"sha256":"MP3_SHA256","durationMs":7000,"textSha256":"TEXT_SHA256"}},
  "entries": [{"id":"source-entry-id","audioKey":"AUDIO_KEY"}]
}
```

The dedicated Worker route uses this fixed complete manifest to return verified clip bytes through Cache API. Its URL is `https://korean-voice.quddnr0107.workers.dev/reader-assistance/f1/v1/AUDIO_KEY.mp3`. It must not fall back to original-voice synthesis or fetch an arbitrary user URL. Worker route implementation and deployment are separate review steps.

## Commands

```bash
python3 -m unittest discover -s test -p reader_assistance_test.py -v
python3 tools/reader_assistance.py plan --data reader-assistance/pilot.json --pilot
python3 tools/reader_assistance.py download-model
python3 tools/reader_assistance.py bake --data reader-assistance/data.json --bucket 0
python3 tools/reader_assistance.py assemble --data reader-assistance/data.json --out complete-manifest.json --publish-catalog
python3 tools/reader_assistance.py attach --data reader-assistance/data.json --catalog complete-manifest.json --out reader-assistance-data-with-audio.json
```

The `attach` command refuses a partial catalog, different source bytes, different recipe, missing row, wrong text hash, or extra/missing audio key. The output keeps every explanation and adds `entry.audio` with F1, recipe ID, the dedicated Worker URL, duration, MP3 checksum, audio key, and text checksum.

The workflow runs on explicit dispatch or the dedicated pilot/bake branches. It is not scheduled daily, and an unchanged data set performs no new synthesis or pack uploads.

## Approved-content staging while source review continues

`tools/reader_assistance_stage.py` accepts a `reader-assistance.v1` export containing only the exact approved explanations. Every supplied entry still needs `review.status: approved` and its current `textSha256`; one unapproved or changed entry stops the entire stage. The exporter must keep the true corpus count and the remaining unexplained count. This wrapper never creates an approval record or silently filters an invalid one.

The separate `reader-assistance-f1-stage.yml` workflow runs on explicit dispatch or the `codex/reader-assistance-f1-stage-20261105` branch. It reads `reader-assistance/staged.json`, uses the unchanged F1 recipe and renderer, and publishes only missing content-addressed packs. It uses the same concurrency group and immutable Release as the final workflow, so staged and final runs cannot race or rebake unchanged clips.

Staging always forces `completeEligible: false`, even if its input happens to contain the full corpus. Its commands are limited to `plan`, `inventory`, `bake`, and an evidence-only `report`. It has no production catalog, attachment, Worker or study-site activation command. Its final job has read-only GitHub permissions. No audio artifact is retained.

When all source explanations are reviewed, the normal production commands must independently validate the complete export and assemble every required audio key. Existing staged clips then satisfy matching keys without loading the TTS model again. Until that complete pass succeeds, staged generation is evidence of generated audio only.

```bash
python3 -m unittest discover -s test -p reader_assistance_stage_test.py -v
python3 tools/reader_assistance_stage.py plan --data reader-assistance/staged.json
python3 tools/reader_assistance_stage.py bake --data reader-assistance/staged.json --bucket 0
python3 tools/reader_assistance_stage.py report --data reader-assistance/staged.json
```

## Version-one limitation

This first production recipe is intentionally frozen. Its source pin includes the complete bake script, so even a transport-only edit changes the recipe hash. Existing packs from a different recipe hash are rejected in this version's namespace. Do not describe an arbitrary settings/code change as an automatic in-place upgrade. A future renderer change needs a reviewed new recipe/namespace (or a separately reviewed compatibility migration); it must not clobber v1 audio. ASR evaluation lives in a separate module and does not change the selected F1 synthesis recipe or its audio keys.

### Independent pilot cross-check

`tools/reader_assistance_asr.py --model medium --export-review-clips reader-assistance-review-clips --publish`
transcribes the same seven immutable MP3 clips with the official
`Systran/faster-whisper-medium` CPU int8 model, without a target-text prompt or
any synthesis. The default remains `--model small`. The accepted model names
are an explicit allowlist, and the actual model revision is recorded.

Push the isolated `codex/reader-assistance-f1-asr-medium-20261105` branch to run
this cross-check. Medium evidence is published as
`reader-assistance/asr-report-medium.json` on `data/reader-assistance-f1-asr-pilot`;
the existing `reader-assistance/asr-report.json` small-model evidence is retained.
The comparison reads the previous small report by immutable Git blob
`4e3726531285baad83cb4d5efc2f224761aa1dc6`, verifies every text/audio identity,
and exports only clips that either model marks for review. The temporary Actions
artifact contains exact MP3 bytes plus both reports and a comparison JSON, with
one-day retention and no audio re-encoding. Model cache saving runs even when
transcription disagreement makes the final job fail; such a failure never marks
the audio or full corpus as approved.

The first successful small-model pilot assessed 106.06 seconds of audio in
20.346 CPU seconds (RTF 0.1918). All seven had matching extracted critical legal
tokens, two had exact normalized transcripts, and five required review for other
transcription disagreements. These are limited pilot measurements, not a claim
that the spoken words were manually checked or that all-corpus accuracy is known.


## Explicit reviewed subset with one unresolved source

`tools/reader_assistance_subset.py` is a separate publication entry point. It does
not change the sealed F1 renderer or relax the full-corpus pipeline. The canonical
`reader-assistance/data.json` remains the exact compiler output: `partial:true`,
6,072 approved entries and one blocked source among 6,073 body rows.

The separately pinned `reader-assistance/reviewed-subset-policy.json` identifies
source 6384, its original/explanation/review hashes, and the unresolved reason. Its
`sourceRowsSha256` is SHA-256 of canonical JSON containing every original body
row as `[sourceIndex, sourceId, originalSha256]`, sorted by source index. Each
approved entry must match its exact approved text and original hash. Adding the
one excluded row must reproduce the whole pinned ledger. The excluded review is
retained unchanged and cannot carry explanation text or audio.

The subset workflow uses the same sixteen CPU hash buckets and reuses existing
clips only when both the exact text and sealed recipe match. The data's source
snapshot may change without regenerating unchanged text. Every included clip must
exist before publication; a missing clip remains a failure. Publication uses only
`data/reader-assistance-f1-reviewed-subset-v1` and the new
`reader-assistance-reviewed-subset-audio.v1` schema. It preserves `complete:false`
and adds `reviewedScopeReady:true` plus the explicit `releaseScope`. These fields
mean that the declared approved scope has synthesized audio; they do not assert
complete source coverage, verified legal correctness, or direct listening.

Attach with:

```sh
python3 tools/reader_assistance_subset.py attach \
  --data reader-assistance/data.json \
  --catalog reader-assistance/audio-manifest.json \
  --out reader-assistance/attached.json
```

The attached result preserves the exclusion and adds F1 audio only to approved
entries. The public Worker permits this new partial schema only when its scope
matches the deployed policy exactly and its entry ledger reproduces the pinned
source hash. Ordinary incomplete manifests remain rejected. Media requests fetch
verified MP3 byte ranges from immutable GitHub Release packs; this route never
loads a model, invokes a paid TTS service, starts a Container, or reads/writes R2.

Text review, completed synthesis, independent ASR sample evidence and actual
screen/playback checks are reported separately. ASR disagreements remain evidence
requiring interpretation and never change an approval record or normalization
rules to manufacture a passing result.
