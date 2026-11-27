#!/usr/bin/env bash
set -euo pipefail
model_dir="$RUNNER_TEMP/bora-qwen"
if [[ "$(python -c "import json; print(json.load(open('bora-production/single-pass-request.json'))['request_id'])")" == 'ms-original-private-recover-054-094-20261127' ]]; then
  python bora-production/no_asr_preview.py validate
  for attempt in 1 2 3; do
    if python bora-production/no_asr_preview.py download --model-dir "$model_dir"; then
      break
    fi
    if (( attempt == 3 )); then exit 1; fi
    sleep "$((attempt * 15))"
  done
else
  python bora-production/no_asr_preview.py download --model-dir "$model_dir"
fi
printf 'BORA_MODEL=%s\n' "$model_dir" >> "$GITHUB_ENV"
