# Run 28: three remaining study chunks

Status (2026-10-03): exact targets identified; scoped recheck preparation only.
**No current audio has been measured or modified. Noise removal and intact speech endings are not yet verified.**

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

This selector was run against the three exactly mapped inputs: selected count **3**, matching IDs **3/3**. This result is scope verification, not an acoustic test. The existing synthetic hum/MP3 regression also passed **9/9** before detector changes (there are no detector changes in this preparation).

Focused selector regressions: **6/6 passed**, including the real matrix planner yielding exactly one `study/훈령` job. The new tests run in the existing Private source safety CI workflow. Python syntax and whitespace checks passed. Independent scope review found no widening of this three-chunk selection.

## Audio validation still required

1. Read only the three existing cached MP3s; preserve original bytes and SHA256 hashes. Do not synthesize on a cache miss.
2. Run the same `hum_tail` detector as `hum_recheck`; record duration, final voiced interval, HNR, low-frequency ratio, and original MP3 checksum for each target.
3. Inspect the final speech and following tail separately. HNR measures periodicity, so a high value alone does not prove an unwanted sound. Praat's official explanation: https://praat.org/manual/Harmonicity.html.
4. If a detached noise tail is confirmed, use only a demonstrably speech-safe correction; if normal speech is being flagged, strengthen detection without cutting audio. Do not tune a threshold solely to make these three cases pass.
5. Recheck encoded MP3 output, compare speech samples/boundaries and duration, and record a listening assessment separately. Missing or inconclusive evidence stays pending.
6. Any eventual same-key replacement needs a playback-cache check: existing responses use one-year immutable caching. No global recipe/revision change is part of this preparation.

## Current blocker

The execution environment's automatic approval review rejected downloading these three originals because `/tts` lookup sends the manuscript text in URL query parameters to `korean-voice.quddnr0107.workers.dev`. The endpoint is configured in the existing `tools/bake.py` recheck code, but the reviewer still requires explicit authorization for that transfer. No alternate route was used to bypass the rejection.

Consequently there is **no before/after audio result**, no new synthesis, no R2 upload, no full-corpus rebake, and no merge/deployment. Obtain that narrowly scoped authorization before continuing the acoustic diagnosis.
