# Bounded public parallel-speed experiment

The owner requested an actual test after the estimated 67 percent reduction. This run compares the same eight original public Korean narration units in one sequential worker versus four workers with two units each. Five standard ubuntu-24.04 runners are the total maximum for this experiment. This is a small pilot, not a forecast for all 303 lectures or a measured result for the existing private 16-unit sample.

The existing selected option-3 FP32Renderer, model revisions, Sohee voice, CPU four threads, two-candidate policy and exact quality gates are reused unchanged. Each worker loads models once. The experiment reads no sealed operating inputs, source/media keys, browser session or production checkpoint. It has no environment binding and no repository write permission because it runs only public synthetic text; it does not bypass any production approval. The existing production workflow, request, protections, assets and healthy run43275117254 remain untouched.

Trigger: a push changing only the dedicated bora-production/benchmark-request.json. Do not alter production request.json to start this experiment. Keep the read-only benchmark concurrency group separate. Do not cancel or duplicate active operating synthesis to collect speed numbers.

Validation includes ten synthetic comparison tests, workflow checks and an exact diff confirming production settings and code are unchanged. Unit timers, load/setup times, actual parallel elapsed envelopes, retry seeds, outcome parity, hardware identities and hashes are recorded. The collector also reads GitHub job creation/completion timestamps to account for runner queueing. Only public JSON timing reports are uploaded; temporary audio and ASR receipts are discarded.

A comparable speedup is emitted only for complete paired coverage, identical input/model identities, equal candidate work and quality outcomes, all units passed, and matching reported CPU models/counts. Otherwise report the elapsed measurements with explicit caveats, not an equivalent-quality performance claim. The pilot has one repetition and excludes final assembly, protected-environment approval, operating checkpoint transport and site rollout. Report aggregate runner time as well as elapsed time, since greater parallelism does not mean less compute.

Inspect the actual Actions comparison job before reporting any speedup. Missing/failed reports must not become a zero duration or a successful comparison. The production scheduling enhancement remains committed at 05089eff and applies only to a later validated production request; this pilot is not a production restart or release.

## Measured outcome (2026-11-13 01:35 UTC; one trial only)

GitHub Actions run [43345048731](https://github.com/quddnr0107-lgtm/korean-voice/actions/runs/43345048731) completed validate, 5 independent workers, and compare successfully. The summary artifact `bora-public-benchmark-summary-43345048731` was published. The result status is **timing_with_caveats**, NOT measured_comparable_pilot.

| Measurement | One serial worker | Four workers (two units each) | Observed difference |
| --- | ---: | ---: | ---: |
| Renderer kernel elapsed envelope | 338.919 s | 127.786 s | 62.296% shorter |
| From each first job step through render finish, including setup | 470.227 s | 252.212 s | 46.364% shorter |
| From GitHub job creation through job completion (including queue) | 478 s | 261 s | 45.397% shorter |
| Aggregate runner execution time (billing-relevant proxy) | 476 s | 919 s | 93.07% **more** runner time |

Integrity checks: identical fixture, plan, recipe, renderer settings and candidate seeds, with 8/8 unit quality checks passing in both paths. **However, the GitHub-hosted workers reported different CPU models (AMD EPYC 9V74, AMD EPYC 7763, Intel Xeon Platinum 8573C), and parallel/serial WAV hashes differed.** Despite matching ASR/EOS outcomes, the comparison is not controlled for hardware or bit-identical audio. The tool therefore emitted `matching_hardware=false`, `same_output_audio_hashes=false`, and `comparable_setup_inclusive_time_reduction_pct=null`. Do not claim that the 45% elapsed advantage is causally established or transferable to 303 lectures.

This is a public, authored eight-unit sample with one repetition; it does not measure existing private TB-001 units, full forty-way production, approval wait, checkpoint transfer, complete-course index or site deployment. No follow-up audio trial is authorized solely to improve the percentage. Production run 43275117254 and its strict quality gates were not touched.
