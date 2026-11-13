# Execution efficiency without changing option 3 audio

Owner request (2026-11-13 KST): reduce elapsed production time through parallelism and reuse while retaining the selected engine/voice/FP32/4-thread configuration and strict quality gates.

## Verified bottlenecks

The existing request for TB-001 produces two large shards under recipe.shard_chars=4000. max-parallel=40 cannot create additional work where the matrix has only two entries. Each shard completes sequentially and its ciphertext bundle is uploaded at the end. Smaller execution groups can finish and persist earlier, at the cost of more setup and checkpoint-download jobs.

The original workflow installed the heavy runtime and downloaded model weights before checking whether authenticated existing checkpoints already covered every assigned unit. It also sorted jobs by unit count rather than approximate spoken workload.

## Implemented changes

- Optional request field execution_shard_chars accepts integers 200 through the unchanged recipe.shard_chars. Omission preserves legacy grouping. It changes only scheduling and request identity, not the source plan, recipe, renderer, sampling, voice, quality criteria, or authenticated per-unit reuse identity.
- The protected source plan and each worker independently reproduce the same groups. Matrix coverage and the 256-job ceiling remain enforced. Longer estimated spoken workloads are placed first; GitHub controls the actual runner start order.
- A lightweight protected offline probe authenticates downloaded checkpoints BEFORE heavy installation/model download. Complete or already-blocked groups with retry_blocked=false skip model preparation and synthesis. Blocked is not passed: the unchanged assembler still rejects them. Missing groups still run the normal renderer and checks. The probe exports only IDs, counts and hashes; its temporary plaintext is removed.
- Same run concurrency protection, maximum 40 workers, environment reviews, network isolation, immutable assets, and full-course index requirement remain unchanged. No production request or active run was restarted by this code change.

## Applying after the currently running recovery test

Do not cancel or duplicate healthy run43275117254 merely to obtain more parallelism. Its checkout remains f4ff95f4632a605bf16942b66e7e6b1d7f08ee2d; it cannot receive these changes retroactively. Preserve its checkpoints and independently inspect real passed/blocked/pending counts after upload.

Require the exact new commit's Bora execution scheduling CI to pass first. For a subsequent small, necessary recovery request, execution_shard_chars=400 is a starting scheduling choice. Preserve campaign_sha256 and retry_blocked=false. Reuse completed authenticated units rather than regenerating the whole lesson. Do not rerun a fully covered but blocked lesson: diagnose the ASR discrepancy first.

For TB-wide production after recovery and output-QA diagnosis pass, execution_shard_chars=2000 is a starting choice for more, shorter jobs under max-parallel=40. Verify the authenticated plan stays <=256 jobs before synthesis. For larger subjects, use bounded lesson_ids batches; do not bypass the matrix ceiling. MS partial-final collection still needs to be verified before launching the large course; this patch does not claim to implement that collector.

Do not alter recipe.shard_chars itself to tune scheduling, because the entire recipe participates in checkpoint identity. Do not increase attempts, weaken ASR matching, or replace the generation engine/model to make the job appear faster.

## Verification scope

19 new synthetic tests include 200 randomized exact-coverage cases, unchanged source/recipe fingerprints, invalid scheduling bounds, tampered public ranges, the matrix ceiling, input-review blocking, and probe decisions. The local orchestration test harness mocked imported cryptography modules; it did NOT validate operating keys or audio. Repository CI runs the complete existing synthetic suite with real cryptography modules and fixed dependencies. Neither result is a real TTS throughput measurement or human listening approval. Report actual runtime speedup only after an optimized production request finishes.
