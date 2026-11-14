#!/usr/bin/env bash
set -euo pipefail
model_dir="$RUNNER_TEMP/bora-qwen"
python bora-production/no_asr_preview.py download --model-dir "$model_dir"
printf 'BORA_MODEL=%s\n' "$model_dir" >> "$GITHUB_ENV"
