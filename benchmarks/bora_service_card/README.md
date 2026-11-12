# Public service-card listening sample

Four newly written public-law paragraphs are synthesized in parallel with the user's
selected profile: Python FP32, four threads, Qwen CustomVoice 1.7B, Sohee. This package
does not contain or read a private lecture manuscript.

`BORA_SAMPLE_PART` selects exactly one `spec.PARTS` paragraph, numbered 0 through 3.
The parent coordinator and its worker inherit the same value. Every artifact records
the selected part, its exact public text and SHA256, CPU identity, package/model hashes,
natural EOS status, actual WAV duration, timings, and peak memory.

The model revision, seed 5192, original explain instruction, main Talker parameters,
Python subtalker sampling, FP32 loading, and raw WAV output are copied from the selected
minute-sample profile. The Python worker, model downloader, and Python requirements are
unchanged. No DSP, trimming, stretching, or native inference is added. Each call keeps
the established 1100-token maximum; cap hits and unverified EOS remain failures.

The four Ubuntu 24.04 matrix jobs have separate working directories and artifacts,
`fail-fast: false`, a 750-second process cap, and a 20-minute job limit. Preparation
installs the established Python package pins and downloads the pinned public model;
native source checkout, compilation, and native artifacts are omitted. The inherited
coordinator accepts an unused native-path placeholder but never opens it in this profile.

The workflow can run only in a public repository on `bora-service-card-20261112`.
Push triggers match only its workflow and `benchmarks/bora_service_card/**`. It requests
`contents: read`, uses no secret or protected environment, and uploads public evidence
with `always()` and one-day retention.

The final listening sample is assembled in paragraph order 0, 1, 2, 3 after the parts are
checked. Its duration is approximate, based on natural speech; this package does not
force a two-minute length. Natural EOS and basic signal checks do not establish factual
or transcript completeness. Parts remain `quality_qualified: false` until review.

Selection can be inspected without model loading:

```bash
BORA_SAMPLE_PART=0 python benchmarks/bora_service_card/run_benchmark.py --dry-run \
  --only-profile python_fp32_t4 --model /tmp/public-model \
  --native /unused-native-placeholder --output /tmp/public-results-part-0
```
