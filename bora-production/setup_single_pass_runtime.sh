#!/usr/bin/env bash
set -euo pipefail
if [[ -n "${ADMIN_LAW_PREMIUM_SOURCE_KEY_B64-}" || -n "${ADMIN_LAW_PREMIUM_MEDIA_KEY_B64-}" ]]; then
  echo 'BORA_SETUP_FAILED: key-bearing dependency installation refused' >&2
  exit 1
fi
sudo apt-get update -qq
sudo apt-get install --yes --no-install-recommends ffmpeg sox libsndfile1
python -m pip install --disable-pip-version-check torch==2.8.0+cpu torchaudio==2.8.0+cpu --index-url https://download.pytorch.org/whl/cpu
python -m pip install --disable-pip-version-check qwen-tts==0.1.1 transformers==4.57.3 soundfile==0.14.0 cryptography==50.0.0 numpy==2.5.4 scipy==1.18.1 huggingface-hub==0.36.2
python -m pip check
command -v ffmpeg
command -v ffprobe
command -v sox
