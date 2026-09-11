# Private TTS source rollout

Paired with yebijun-muhanhoidok234 PR #625.

- `tools/chunks.mjs` keeps fetching public segmentation scripts, but the three source JSON files come only from the authenticated yebijun source API.
- Tokens are short-lived GitHub OIDC tokens with audience `yebijun-tts-source`; the receiving Worker restricts repository/owner IDs, main branch and the two manual bake/prune workflows.
- The public workflow no longer uploads `chunks.json`. Each runner reconstructs it privately and verifies the plan's SHA-256 digest before baking or pruning. Source changes during a run stop that job.
- Old Container bulk-bake enqueue/resume is retired, and a pending bulk alarm stops instead of synthesizing. Normal R2 hits are served directly; synthesis starts only after a confirmed R2 miss. R2 errors/missing bindings return 503, and HEAD probes never synthesize.
- Error logging no longer prints the start of a lecture sentence.

Deploy the yebijun source API and upload its data before running a new bake/prune workflow. Do not trigger a full bake merely to verify the migration.

Required verification: run the source plan on main without publishing private text, then use a small explicitly selected bake sample to verify OIDC upload and playback. Local source-reader tests cannot establish live Cloudflare authentication.

Historical `chunks` artifacts are not removed by changing code. `Remove historical private-text artifacts` is a manual dry-run-by-default workflow that deletes only completed-run artifacts named `chunks`. It skips active runs. Old public logs may also contain sentence excerpts and require review; already downloaded copies cannot be recalled.
