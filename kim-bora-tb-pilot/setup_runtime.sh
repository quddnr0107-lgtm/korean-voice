#!/usr/bin/env bash
# Shared by the public smoke job and protected rendering jobs. No keys here.
set -euo pipefail
if [[ -n "${ADMIN_LAW_PREMIUM_SOURCE_KEY_B64-}" || -n "${ADMIN_LAW_PREMIUM_MEDIA_KEY_B64-}" ]]; then
  echo 'BORA_SETUP_FAILED: key-bearing dependency installation refused' >&2
  exit 1
fi
sudo apt-get update -qq
sudo apt-get install --yes --no-install-recommends ffmpeg sox libsndfile1
python -m pip install --disable-pip-version-check torch==2.8.0+cpu torchaudio==2.8.0+cpu --index-url https://download.pytorch.org/whl/cpu
python -m pip install --disable-pip-version-check qwen-tts==0.1.1 transformers==4.57.3 faster-whisper==1.2.1 soundfile==0.14.0 cryptography==50.0.0 numpy==2.5.4 scipy==1.18.1 onnxruntime==1.31.1
python -m pip check
command -v ffmpeg
ffmpeg -version
command -v sox
sox --version
