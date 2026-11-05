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

`korean-voice` is public. The workflow uses standard `ubuntu-latest` CPU runners, with at most four synthesis jobs in parallel. GitHub's [Actions billing documentation](https://docs.github.com/en/actions/concepts/billing-and-usage) states that standard hosted runners are free for public repositories. Larger/GPU runners are not used.

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

## Version-one limitation

This first production recipe is intentionally frozen. Its source pin includes the complete bake script, so even a transport-only edit changes the recipe hash. Existing packs from a different recipe hash are rejected in this version's namespace. Do not describe an arbitrary settings/code change as an automatic in-place upgrade. A future renderer change needs a reviewed new recipe/namespace (or a separately reviewed compatibility migration); it must not clobber v1 audio. ASR evaluation lives in a separate module and does not change the selected F1 synthesis recipe or its audio keys.
