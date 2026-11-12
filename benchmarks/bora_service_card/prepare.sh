#!/usr/bin/env bash
# Public Python-only preparation using the established runtime and model pins.
set -euo pipefail
BENCH_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
: "${RUNNER_TEMP:?RUNNER_TEMP is required}"
: "${BORA_SAMPLE_PART:?BORA_SAMPLE_PART is required}"
case "$BORA_SAMPLE_PART" in
  0|1|2|3) ;;
  *) echo 'Unknown public service-card paragraph' >&2; exit 2 ;;
esac
BORA_WORK="$RUNNER_TEMP/bora-service-card-part-$BORA_SAMPLE_PART-work"
BORA_RESULTS="$RUNNER_TEMP/bora-service-card-part-$BORA_SAMPLE_PART-results"
mkdir -p "$BORA_WORK" "$BORA_RESULTS"
export HF_HUB_DISABLE_IMPLICIT_TOKEN=1 HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1

sudo apt-get update -qq
sudo apt-get install --yes --no-install-recommends ffmpeg sox libsndfile1
python -m pip install --disable-pip-version-check torch==2.8.0+cpu torchaudio==2.8.0+cpu --index-url https://download.pytorch.org/whl/cpu
python -m pip install --disable-pip-version-check -r "$BENCH_DIR/requirements.txt"
python -m pip check
python -m pip freeze > "$BORA_RESULTS/pip-freeze.txt"
dpkg-query -W ffmpeg sox libsndfile1 > "$BORA_RESULTS/os-packages.txt"

python "$BENCH_DIR/download_model.py" --model "$BORA_WORK/model" --report "$BORA_RESULTS/model-manifest.json"
printf 'BORA_BENCH_MODEL=%s\nBORA_BENCH_OUTPUT=%s\n' \
  "$BORA_WORK/model" "$BORA_RESULTS" >> "$GITHUB_ENV"
