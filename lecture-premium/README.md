# Sealed premium lecture generation

Dedicated, manually invoked batch for 25 TB, 78 DR and 34 RL lessons. Existing administrative-law assets, workflows and cryptographic formats are not modified.

- Public standard `ubuntu-latest`, maximum parallelism 40, no cancellation of other runs. GitHub enforces the account-wide limit.
- No Actions artifact/cache writes. Public repository inputs and dedicated Release outputs are authenticated ciphertext only.
- Source encryption uses the existing pinned public receiver. Source/media master keys stay in the approved protected environment; they are never read locally, printed or exported into public files.
- Inference has ONNX telemetry disabled before import and runs in an isolated Linux network namespace. Model download and ciphertext delivery are separate keyless steps.
- Generic Supertonic 3 F2 preset, 16 steps, approved sample-derived speed/pause treatment; no voice cloning.
- Original text/offsets are preserved. Reversible pronunciation edits and actual segment timings are in encrypted private metadata.
- Completed encrypted outputs are immutable. Only failed jobs need retry; encrypted per-lesson checkpoints resume completed chunks. Every existing output is authenticated and matched to its exact input before reuse.
- No claim of listening or ASR verification. Signal, silence, duration, finiteness and clipping checks are recorded per chunk and per completed MP3.

## Dispatch

Register this workflow on the default branch, then select only `integrated-lectures-premium-20261028` for the actual dispatch. Type `BAKE-137-PREMIUM`. The plan and protected renderer reject other refs, and authorization.json must contain the separately confirmed key-purpose approval. Required environment review remains in effect.

The final encrypted Release index is created only when all 137 complete outputs have passed ciphertext/schema verification. End-user playback still requires an authenticated premium server that decrypts the new media format; this batch does not publish a public playable MP3 or grant browser access to a key.

## Formats

Input: `lecture-ingress/v1`; existing receiver wrapping is retained only for unwrapping the pinned receiver inside the protected job.

Media: `integrated-lectures-media/v1`, key ID `integrated-lectures-media-v1`, namespaces `integrated-lectures/v1/tongbang`, `integrated-lectures/v1/directive`, `integrated-lectures/v1/reserve-law`.

Release: `integrated-lectures-v2-20261028`. Index: `integrated-lecture-index/v2`. Audio uses `integrated-lecture-pack/v2`, with separately authenticated `integrated-lecture-audio-part/v2` independent MP3 parts (at most 60 seconds / 2 MiB each), bundled in one ciphertext object per lesson. Content/checkpoints retain the new lecture media/v1 format. No full-lecture plaintext MP3 is produced.

All outputs are **AI-generated speech / AI 합성 음성**. The model is free to run locally but subject to BigScience OpenRAIL-M use conditions; it is not an MIT-licensed model. The official inference helper is separately MIT licensed. Pinned model/helper license texts are included.

Model: https://huggingface.co/Supertone/supertonic-3 at the pinned revision in model-pins.json (BigScience OpenRAIL-M). Official helper: supertone-oss-archive/supertonic at pinned revision (MIT). No model weights are committed here.

The pinned root index is capped at 128 KiB and contains only `{id, descriptor_url, sha256}` references. Each immutable per-lesson descriptor is capped at 512 KiB and its exact bytes are verified against the root SHA before pack or content retrieval. This avoids parsing the entire course pack catalog on each authenticated request.
