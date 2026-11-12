#!/usr/bin/env bash
# Run only on the disposable public Ubuntu runner. No manuscript or key input.
set -euo pipefail
BENCH_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
: "${RUNNER_TEMP:?RUNNER_TEMP is required}"
: "${BORA_MINUTE_PROFILE:?BORA_MINUTE_PROFILE is required}"
case "$BORA_MINUTE_PROFILE" in
  native_bf16_t4|native_int8_t4|python_fp32_t4) ;;
  *) echo 'Unknown public minute profile' >&2; exit 2 ;;
esac
BORA_WORK="$RUNNER_TEMP/bora-minute-$BORA_MINUTE_PROFILE-work"
BORA_RESULTS="$RUNNER_TEMP/bora-minute-$BORA_MINUTE_PROFILE-results"
mkdir -p "$BORA_WORK" "$BORA_RESULTS"
export HF_HUB_DISABLE_IMPLICIT_TOKEN=1 HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1
export GIT_TERMINAL_PROMPT=0

sudo apt-get update -qq
sudo apt-get install --yes --no-install-recommends build-essential libopenblas-dev ffmpeg sox libsndfile1
python -m pip install --disable-pip-version-check torch==2.8.0+cpu torchaudio==2.8.0+cpu --index-url https://download.pytorch.org/whl/cpu
python -m pip install --disable-pip-version-check -r "$BENCH_DIR/requirements.txt"
python -m pip check
python -m pip freeze > "$BORA_RESULTS/pip-freeze.txt"
dpkg-query -W build-essential gcc libopenblas-dev libopenblas0-pthread ffmpeg sox libsndfile1 > "$BORA_RESULTS/os-packages.txt"

NATIVE_REPO=https://github.com/gabriele-mastrapasqua/qwen3-tts
NATIVE_PIN=ef339be58a778b062e1c14382347964552eae007
NATIVE_DIR="$BORA_WORK/native"
git init "$NATIVE_DIR"
git -C "$NATIVE_DIR" remote add origin "$NATIVE_REPO"
git -C "$NATIVE_DIR" -c credential.helper= -c core.askPass= fetch --depth=1 origin "$NATIVE_PIN"
git -C "$NATIVE_DIR" checkout --detach FETCH_HEAD
test "$(git -C "$NATIVE_DIR" rev-parse HEAD)" = "$NATIVE_PIN"
bash "$NATIVE_DIR/tools/source_fingerprint.sh" > "$BORA_RESULTS/native-source-fingerprint.txt"
git -C "$NATIVE_DIR" diff --exit-code
gcc --version > "$BORA_RESULTS/compiler.txt"
gcc -O2 -Wall -Wextra -I "$NATIVE_DIR" -I "$NATIVE_DIR/third_party/ingot/include" \
  -c "$BENCH_DIR/native_observer.c" -o "$BORA_WORK/native_observer.o"
# The only addition to the upstream link is observation outside its math kernels.
make -C "$NATIVE_DIR" -j4 blas \
  "LDLIBS=$BORA_WORK/native_observer.o -Wl,--wrap=qwen_tts_load_ex -Wl,--wrap=qwen_tts_generate -lm -lpthread -lopenblas" \
  2>&1 | tee "$BORA_RESULTS/native-build.log"
sha256sum "$NATIVE_DIR/qwen_tts" "$BENCH_DIR/native_observer.c" > "$BORA_RESULTS/native-binary-sha256.txt"
git -C "$NATIVE_DIR" diff --exit-code
cp "$NATIVE_DIR/.source_fingerprint" "$BORA_RESULTS/native-build-source-fingerprint.txt"
"$NATIVE_DIR/qwen_tts" --caps > "$BORA_RESULTS/native-caps.txt" 2>&1

python "$BENCH_DIR/download_model.py" --model "$BORA_WORK/model" --report "$BORA_RESULTS/model-manifest.json"
printf 'BORA_BENCH_MODEL=%s\nBORA_BENCH_NATIVE=%s\nBORA_BENCH_OUTPUT=%s\n' \
  "$BORA_WORK/model" "$NATIVE_DIR/qwen_tts" "$BORA_RESULTS" >> "$GITHUB_ENV"
