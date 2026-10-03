# Run 28: three remaining study chunks

Status (2026-10-03): the three existing R2 MP3s were rechecked after explicit lookup authorization.
**The legacy detector flags a voiced part of the ending despite a strong unvoiced release following it. The detector was corrected; all three MP3s and their decoded samples remain unchanged.**
This is an acoustic/code finding. Subjective listening was not performed because this execution environment does not accept audio input.

## Evidence and scope

- Site repository main: `quddnr0107-lgtm/yebijun-muhanhoidok234` at `6857d262dac97f936f793c0402a5da60cbb6b191` (PR #759).
- Voice repository main: `829ec017724c8a31a8586b0150369f4f8116c0ed`.
- Bake run: https://github.com/quddnr0107-lgtm/korean-voice/actions/runs/37007646702 (run 28).
- Six study/훈령 jobs generated 362 chunks with 0 generation failures. Three chunks were still flagged after four single-item retries; that is a detector result, not a listening verdict.
- Current main appendix speech was processed through the site's own `SpeechText.줄글`, `SpeechText.읽기용`, and `LiveTTS.segment`. All three exact text hashes and rates matched. Each is shared by the 26년 후반기 and 27년 전반기 source editions.
- Manuscript text is intentionally absent from this public report and workflow logs.

| Text SHA1 | Rate | Source article | Run 28 job |
|---|---:|---|---|
| `4ecc24ebbf11f3cfd682d1da9ddec1d85ead40a7` | 0.89 | 훈령 본훈 2-23, 별표 6 | `110839701867` (study/훈령 3/6) |
| `fd26664592c840322601909027ec5892fda6f639` | 0.88 | 훈령 본훈 1-14 | `110839701939` (study/훈령 4/6) |
| `310d3d9f8183a17caeedae8407a369e8ff7e06bc` | 0.90 | 훈령 본훈 2-23, 별표 6 | `110839701939` (study/훈령 4/6) |

Existing playback recipe: `female`, `k2`, 16 steps. R2 keys calculated with the repository's canonical key function:

| Log ID | R2 key |
|---|---|
| `4ecc24ebbf` | `tts/female/966ad1d946039b92d0ba9a5c8edde34b01836d3d.mp3` |
| `fd26664592` | `tts/female/0c82864dfe5aef97ce756834bf989fc81968bd20.mp3` |
| `310d3d9f81` | `tts/female/8917db69f76c2802035a91d725067bf609fbd60f.mp3` |

## Why a scope guard is needed

The current `hum_recheck` flag rechecks every cached chunk assigned to the selected jobs. It does not itself select these three IDs. Setting `kind=study` alone would inspect 8,116 study chunks, and `force` could regenerate the selected scope regardless of its condition.

The follow-up adds a fail-closed `chunk_ids` selector before matrix planning and before bake model/network work. The target input for this task is:

```text
4ecc24ebbf,fd26664592,310d3d9f81
```

Unknown, ambiguous, malformed, or repeated IDs must stop the run. Targeted runs must not prune or silently truncate their selection with `start` or `limit`. Existing full-source hash verification remains in place.

Model-free scope check (local authorized source file; does not fetch audio):

```sh
python3 tools/hum.py --select-chunks chunks.json --kind study --law 훈령 \
  --chunk-ids 4ecc24ebbf,fd26664592,310d3d9f81
```

This selector was run against the three exactly mapped inputs: selected count **3**, matching IDs **3/3**. The original synthetic hum/MP3 regression passed **9/9** before the detector correction.

Focused selector regressions: **6/6 passed**, including the real matrix planner yielding exactly one `study/훈령` job. The new tests run in the existing Private source safety CI workflow. Python syntax and whitespace checks passed. Independent scope review found no widening of this three-chunk selection.

After correction, `test/hum_test.py` passes **18/18**: synthetic voiced-release false positives in WAV and 48 kbps MP3, genuine terminal hum after weak noise/click/release, and sample immutability. Selector plus private-source regressions pass **9/9**. The audio regressions are now also run in PR CI, without loading a voice model.

## Actual three-file result

The user explicitly approved these three lookups on 2026-10-03. HEAD confirmed each existing R2 object before GET. All responses reported `X-TTS-Cache: r2`, recipe `k2`, at 06:57:55–56 UTC. No cache-miss synthesis was requested.

| ID | Duration | Legacy flag → corrected flag | Active release after last voiced point | Low-frequency fraction in following release |
|---|---:|---|---:|---:|
| `4ecc24ebbf` | 3.000 s | true → false | 170 ms | 1.33% |
| `fd26664592` | 6.984 s | true → false | 178 ms | 1.48% |
| `310d3d9f81` | 5.232 s | true → false | 189 ms | 1.05% |

The original rule used only the last pitch-tracked segment and its preceding 300 ms. A voiced ending can meet its HNR/low-frequency thresholds while the actual speech continues after that segment. The measured continuation is broadband, rather than a detached terminal low tone. Independent analysis reproduced this finding. Cutting at the flagged voiced segment would remove part of the ending.

The correction is confined to `server/voice_shape_k2.py` detection. After the last pitch point, it leaves a 40 ms pitch-window margin and requires four consecutive 20 ms frames with both:

- RMS above the peak frame's level minus 30 dB;
- less than 25% of spectral energy below 400 Hz.

If that sustained unvoiced release exists, the earlier voiced segment is not classified as terminal hum. No per-text exception, threshold change to the original HNR/low-ratio gates, audio trim, normalization, re-encoding, recipe revision, or cache invalidation was added.

The guard represents continuing speech evidence. It is not a general guarantee that every possible noise is absent. HNR itself measures periodicity, not whether the signal is wanted: https://praat.org/manual/Harmonicity.html. The 75 Hz pitch analysis uses a 40 ms effective window: https://praat.org/manual/Sound__To_Pitch___.html.

Detailed measurements and original SHA256 hashes are in [`hum-run28-verification.json`](hum-run28-verification.json), bound to the detector file's SHA256. The same decoded inputs were checked against the old and new detector: **3 → 0 flags, 3/3 MP3 byte hashes unchanged, 3/3 decoded arrays unchanged, 0 samples cut, 0 ms duration change**. This proves this correction cannot clip speech; it does not substitute for a subjective listening review.

## Execution and remaining status

- Cached originals inspected: **3**. Synthesis: **0**. R2 uploads: **0**. Full-corpus rechecks/rebakes: **0**.
- No merge/deployment was performed. The correction is in the PR branch.
- A separate full `hum_recheck` run is unnecessary for this task: its exact MP3 decode + `hum_tail` operation has been performed locally on the selected three inputs. Re-generating these recordings would not fix the observed detector mistake.
- Human listening is unverified and explicitly separate from the reproducible acoustic and byte-preservation results.
