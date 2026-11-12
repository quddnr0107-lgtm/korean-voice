# Public Korean TTS CPU speed screen

This independent experiment compares four requested execution profiles on **one standard
Ubuntu 24.04 GitHub runner**. It is a short screening run, not a production rendering job
or an audio-quality qualification. It has no manuscript, key, protected environment,
paid runner, external publishing step, or reference voice recording.

Copy this directory to `benchmarks/bora_speed/` and the companion workflow to
`.github/workflows/bora-speed-public.yml` in the review branch. The workflow is restricted
to the public repository's `bora-speed-research-20261112` branch. It can start from a push
that changes these paths or from `workflow_dispatch`. This draft itself does not push or
dispatch anything.

## Fixed experiment

The public text is `안녕하세요. 오늘도 차근차근 함께 공부해요.` (24 Unicode characters,
including spaces and punctuation). It is the existing public smoke-test sentence.
The original English `explain` instruction is preserved verbatim in `spec.py`.

| Order | Requested engine / weight precision | Threads |
|---:|---|---:|
| 1 | Native C BF16 | 4 |
| 2 | Native C INT8 Talker and Code Predictor | 4 |
| 3 | Python BF16 | 2 |
| 4 | Python FP32 | 4 |

Every trial uses `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice` at model revision
`0c0e3051f131929182e2c023b9537f8b1c68adfe`, Sohee, Korean, seed 5192,
temperature 0.8, top-p 0.9, top-k 50, repetition penalty 1.05, and a maximum of
160 new tokens/frames. There is one trial per profile. Each profile receives a fresh
subprocess, exits before the next begins, and has a 210-second wall limit **including
imports, model loading, and generation**. Timeout is recorded as failure; the next
profile still runs if the global time budget allows.

Preparation happens once. The job has a 20-minute hard boundary. A deadline 18 minutes
after the first step limits the coordinator, which preserves another 35 seconds for
finalization; the remaining job time is for artifact upload. If preparation consumes
too much time, later profiles get a reduced limit or `not_run_budget_exhausted`, not
an invented result. A timeout during runner setup still permits the `always()` upload
step, subject to GitHub's overall job cancellation boundary.

## What is measured

- `cold_model_load_seconds`: fresh-process model-load call. Python measures
  `Qwen3TTSModel.from_pretrained`; native measures the whole `qwen_tts_load_ex`, including
  the INT8 preparation performed there. Model download, Python imports, compiler work,
  and logging are outside this metric. The filesystem page cache is **not** cleared;
  download and earlier trials can warm it. This is process-cold, not disk-cold.
- `tts_seconds`: the original generation call including text processing and audio decode.
  It excludes writing the output WAV and unloading the model.
- `audio_seconds`: actual saved WAV samples / sample rate, never text-based estimates.
- `tts_rtf`: TTS seconds / that trial's audio seconds. `load_plus_tts_rtf` and
  `process_rtf` separately show load and startup effects.
- `max_rss_kib`: Linux `wait4` peak RSS for the actual child process, including failed
  and timed-out trials. The summary also gives MiB.
- CPU model, vendor/family/model/stepping, CPU flags, affinity, topology (`lscpu`), cgroup
  CPU/memory limits, runner image version, all direct Python pins, and `pip freeze`.
- Native source fingerprint from upstream `tools/source_fingerprint.sh`, exact source
  commit, compiler and build flags, observer source hash, and final binary SHA256.

Each native trial prints upstream caps, `[FLAGS]`, and the resolved dispatch map **inside
the actual generation process**, after its load configuration. The small
`native_observer.c` is linked with GNU ld `--wrap`; it brackets existing load/generation
calls and does not alter engine code or generation arguments. Its code hash and the
instrumented binary hash are retained. `dispatch.json` describes resolved gates. It is
not a shape census or proof that each eligible kernel actually executed. No shape census,
profiler, server, GPU, speculative decoding, or automatic engine tuning is enabled.
The labels BF16/INT8 name requested weight modes, not a claim about every kernel's ISA.

## Explicit failure and interpretation rules

Native output must contain a matching natural `EOS at frame N` before 160 frames.
Python observes the original Talker's returned sequence without changing it; its final
token must be EOS and sequence length must stay below the cap. Hitting the cap,
missing EOS evidence, a timeout, missing/empty/nonfinite/silent WAV, or an engine error
is a failure. Such trials retain their evidence and audio where available but receive
no eligible RTF result. A failed profile makes the final benchmark step fail **after all
remaining profiles have had a chance to run**. This cannot silently accept a faster,
truncated file.

Completion means the generation ended naturally and the file passed basic signal
checks. It does not prove that every Korean word was spoken correctly. No ASR model is
downloaded, and no production ASR or listening gate is bypassed. All results have
`quality_qualified: false`; listen and independently check the chosen candidate before
changing production. The WAVs are raw outputs with no trimming, stretch, loudness DSP,
or seam changes.

**Sampling is not fully equivalent across the two engines.** The pinned native Code
Predictor uses argmax (`qwen_tts_code_predictor.c`, `cp_lm_argmax` and scalar path), while
the original Python profile samples its subtalker with temperature .9, top-p 1, top-k 50.
The benchmark preserves that Python behavior. The main Talker parameters match, but
seed implementations, arithmetic, tokenizer/prompt implementation, and output lengths
can differ. Native INT8 also changes arithmetic. A speed result therefore identifies
a candidate engine; it does not establish bitwise equivalence or equal voice quality.
The one fixed-order trial is affected by page-cache and thermal/order effects. Do not
claim a production speedup from it without a longer representative verification.

## Dependencies and pinning

Python is 3.12, using exactly the production public-smoke direct pins:

| Package | Version |
|---|---|
| torch | 2.8.0+cpu |
| torchaudio | 2.8.0+cpu |
| qwen-tts | 0.1.1 |
| transformers | 4.57.3 |
| faster-whisper | 1.2.1 |
| soundfile | 0.14.0 |
| cryptography | 50.0.0 |
| numpy | 2.5.4 |
| scipy | 1.18.1 |
| onnxruntime | 1.31.1 |

The CPU PyTorch packages use `https://download.pytorch.org/whl/cpu`; the other packages
use the standard pip index. Transitive Python versions are captured by `pip freeze`;
they are not asserted to be a complete lockfile. Ubuntu packages are `build-essential`,
`libopenblas-dev`, `ffmpeg`, `sox`, `libsndfile1`; resolved package versions are saved.
Native upstream is `https://github.com/gabriele-mastrapasqua/qwen3-tts`, commit
`ef339be58a778b062e1c14382347964552eae007`. It is fetched without stored credentials,
built with upstream `make -j4 blas` host-appropriate defaults, and instrumented only at
link time. No upstream updates or submodule branch pulls occur.

Model files come only from the pinned public model revision with `token=False`.
Every file's SHA256 and the two weight-file sizes are recorded. Inference uses
`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, and local paths. Existing `QWEN_*` tuning
variables and model tokens are removed from each child environment. Native explicitly
sets non-streaming prompt layout and disables EOS boosting and compose substitutions.

## Files produced

The one-day public artifact contains `run_manifest.json`, `summary.md`, model hashes,
dependency/build provenance, and one directory per profile with `metrics.json`, partial
or complete `worker.json`, raw `audio.wav` when available, `stdout.log`, `stderr.log`,
and native `dispatch.json` when model loading reached instrumentation. Model weights
are outside the artifact directory. The workflow requests only `contents: read`, does
not use a protected environment or any secret, and stores no private lecture input.

For a no-inference plan check:

```bash
python benchmarks/bora_speed/run_benchmark.py --dry-run \
  --model /tmp/public-model --native /tmp/native/qwen_tts --output /tmp/public-results
```

Do not run `prepare.sh` on the local production checkout. The draft has been prepared
for the disposable runner and requires review before its branch is pushed.
