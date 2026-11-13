# Original Kim Bora CPU optimization screening

This isolated public experiment keeps Qwen3-TTS 1.7B CustomVoice revision
`0c0e3051f131929182e2c023b9537f8b1c68adfe`, Sohee, Korean and the existing
Kim Bora explanation instruction. It never uses protected manuscripts, keys,
production requests, alternate voices, voice conversion or audio tempo changes.

Two independent jobs compare candidates only against their own same-host baseline:

- **Python FP32:** the only change is disabling unused inner Code Predictor
  hidden-state history. The outer history carrying codec IDs remains enabled.
  Balanced paired trials reset the same seed and compare every codebook's integer
  codes, raw FLOAT PCM, sample count and normal EOS. Timings are not treated as an
  accepted gain unless all measured pairs and repeated baselines match exactly.
- **Existing native BF16 engine:** the fixed clean native source is built once;
  baseline and prefill row-packing/QKV-sharing/chunk-width environment variants
  run sequentially. Hardware and dispatched kernels are checked. Unsupported
  CPUs are recorded as unsupported, rather than described as tested paths.
  Raw PCM, counts, EOS and repeated baseline stability gate speed comparisons.

The native engine's existing code-predictor sampler differs from Python.
These are independent within-engine experiments, not a native-versus-Python
quality or speed comparison. No candidate is promoted to production automatically.

`download_same_model.py` verifies every required asset against known SHA256
digests at the fixed revision. Model preparation and loading are reported
separately from synthesis. Runtime hooks add equal observation overhead to both
Python arms. Short public fixtures cannot establish full-course speed or quality.

Run lightweight tests without model imports:

```sh
python -m unittest discover -s benchmarks/original_speed -p 'test_*.py' -v
```

The branch-scoped `original-speed-lab.yml` runs on a request marker change.
Its checkout and artifact actions use the repository's existing pinned revisions.
Raw public evidence is uploaded as workflow artifacts rather than committed.
