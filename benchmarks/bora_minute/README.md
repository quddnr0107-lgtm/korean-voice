# Public one-minute Korean listening comparison

Generate three raw listening samples from the same public Korean paragraph. This package
copies the already exercised speed-screen implementation, preserving its model revision,
original explain instruction, seed, main Talker sampling, engine code, observation hooks,
package pins, and natural-EOS failure rules. No production input or configuration is changed.

| Listening label | Profile | Threads |
|---|---|---:|
| 1 | native_bf16_t4 | 4 |
| 2 | native_int8_t4 | 4 |
| 3 | python_fp32_t4 | 4 |

The user's current preference remains sample 3. This experiment supplies longer samples
for comparison; it does not change that preference or select a production engine.
Python BF16 is excluded.

## Fixed input and limits

`spec.py` contains the exact shared 338-character public paragraph. Every matrix job
records its UTF-8 SHA256. The sample is expected to last approximately one minute; the
actual duration depends on the generated speech and is measured from WAV sample count.
There is no trimming or time stretching to force a duration.

- Model: `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`.
- Model revision: `0c0e3051f131929182e2c023b9537f8b1c68adfe`.
- Native source: `ef339be58a778b062e1c14382347964552eae007`.
- Sohee, Korean, original explain instruction, seed 5192.
- Temperature .8, top-p .9, top-k 50, repetition penalty 1.05.
- Maximum 1100 tokens/frames (about 88 seconds of native acoustic frames).
- Each generation subprocess has a 1050-second wall cap, including loading and startup.
- Each job has a 25-minute timeout. Preparation has a 7-minute step cap; a global
  24-minute deadline reserves finalization/upload time and can reduce the inference cap
  if preparation is unusually slow.

Native EOS boosting and compose substitutions remain disabled. Native Code Predictor
uses argmax while Python preserves its original subtalker sampling (.9/1/50); matching
main-Talker settings and seed do not imply identical samples across implementations.

## Isolated parallel execution

The workflow is `.github/workflows/bora-minute-public.yml`. Only public repositories on
branch `bora-minute-comparison-20261112` are allowed. Push triggers are limited to that
workflow and `benchmarks/bora_minute/**`; manual dispatch is also available.

Three `ubuntu-24.04` matrix jobs run with `max-parallel: 3` and `fail-fast: false`. Each
uses `--only-profile`, a profile-specific work/results directory, and a distinct artifact
name. They may receive different CPU models. CPU identity and flags are retained for
each result; this is a listening comparison, not a same-host speed-ratio experiment.
The workflow requests `contents: read`, uses no secret or protected environment, and
uploads the profile's artifact with `always()` and one-day retention.

`prepare.sh` reuses the proven package installation, public model download, upstream
native build, and observer link commands. Only profile-specific temporary directories
and profile validation differ from the original preparation script. Both engines are
prepared in each job to keep that proven preparation path unchanged.

## Evidence and failure handling

Each artifact contains the raw WAV when generation completes, manifest, CPU information,
model and binary hashes, package versions, native in-process flags/dispatch, stdout/stderr,
load/TTS/full-process time, actual duration, RTF, peak RSS, and EOS/cap status. The
1100-token cap cannot turn a truncated file into a successful result. Timeouts, missing
natural EOS, invalid signal, and engine failures remain explicit failures. Artifacts are
uploaded even for a failed profile when GitHub permits the final step to run.

All samples remain `quality_qualified: false` until independent ASR/listening review.
No production ASR gate is weakened or bypassed. Model weights are not uploaded.

Selection can be checked without loading a model:

```bash
python benchmarks/bora_minute/run_benchmark.py --dry-run \
  --only-profile python_fp32_t4 \
  --model /tmp/public-model --native /tmp/native/qwen_tts --output /tmp/public-results
```

The package itself does not push, dispatch, publish, or modify previous results.
