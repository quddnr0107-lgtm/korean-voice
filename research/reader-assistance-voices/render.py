#!/usr/bin/env python3
"""Generate only the three requested previews using the repository synthesizer."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TEXT = "이 항은 보충역 편입 대상이어도 예외에 해당하면 보충역으로 편입하지 않는다는 뜻입니다."
ASSET_FILES = (
    "onnx/duration_predictor.onnx", "onnx/text_encoder.onnx", "onnx/tts.json",
    "onnx/unicode_indexer.json", "onnx/vector_estimator.onnx", "onnx/vocoder.onnx",
    "voice_styles/F1.json", "voice_styles/F3.json", "voice_styles/F5.json",
)

def git_blob(path):
    data = path.read_bytes()
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()

def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--assets", default="supertonic3")
    parser.add_argument("--helper")
    parser.add_argument("--out", default="preview-out")
    args = parser.parse_args()
    plan = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    text = (ROOT / plan["textFile"]).read_text(encoding="utf-8").strip()
    if text != TEXT or [v["style"] for v in plan["voices"]] != ["F1", "F3", "F5"]:
        raise SystemExit("The fixed preview text or styles changed.")
    expected = dict(rate=1.05, baseSpeed=1.0, steps=16, emotion="neutral",
                    noProfile=True, seed=20261105, gainDb=-1, pitchSt=0,
                    brightDb=0, punch=0)
    if plan["settings"] != expected:
        raise SystemExit("The fixed common settings changed.")
    for path, expected_blob in plan["sourceGitBlobs"].items():
        if git_blob(ROOT / path) != expected_blob:
            raise SystemExit("Reviewed synthesizer source changed: " + path)
    if args.plan_only:
        print(json.dumps({"styles": ["F1", "F3", "F5"], "text": text,
                          "settings": expected}, ensure_ascii=False))
        return
    if not args.helper:
        parser.error("--helper is required for synthesis")
    assets = Path(args.assets).resolve()
    helper_dir = Path(args.helper).resolve()
    if git_blob(helper_dir / "helper.py") != plan["helper"]["gitBlob"]:
        raise SystemExit("Pinned helper content does not match the reviewed blob.")
    asset_hashes = {}
    for relative in ASSET_FILES:
        path = assets / relative
        if not path.is_file() or path.stat().st_size == 0:
            raise SystemExit("Missing asset: " + relative)
        if path.suffix == ".json":
            json.loads(path.read_text(encoding="utf-8"))
        elif path.stat().st_size < 1024:
            raise SystemExit("Invalid ONNX asset: " + relative)
        asset_hashes[relative] = sha256(path)
    import numpy as np
    import onnxruntime as ort
    import soundfile as sf
    import imageio_ffmpeg
    ort.disable_telemetry_events()
    output = Path(args.out).resolve()
    output.mkdir(parents=True, exist_ok=True)
    rendered = []
    for voice in plan["voices"]:
        np.random.seed(expected["seed"])
        wav_path = output / voice["wav"]
        mp3_path = output / voice["mp3"]
        sys.argv = [
            "tools/synth-supertonic.py", "--assets", str(assets),
            "--helper", str(helper_dir), "--text-file", str(ROOT / plan["textFile"]),
            "--style", voice["style"], "--no-profile", "--emotion", "neutral",
            "--rate", "1.05", "--base-speed", "1.0", "--steps", "16",
            "--gain-db", "-1", "--pitch-st", "0", "--bright-db", "0",
            "--punch", "0", "--out", str(wav_path), "--mp3",
        ]
        runpy.run_path(str(ROOT / "tools/synth-supertonic.py"), run_name="__main__")
        wave, sample_rate = sf.read(wav_path, dtype="float32")
        duration = len(wave) / sample_rate
        if sample_rate != 44100 or not 1 < duration < 30:
            raise SystemExit("Unexpected sample rate or duration: " + voice["style"])
        if not np.isfinite(wave).all() or np.max(np.abs(wave)) < 0.01:
            raise SystemExit("Invalid or silent preview: " + voice["style"])
        if not mp3_path.is_file() or mp3_path.stat().st_size == 0:
            raise SystemExit("Missing MP3: " + voice["style"])
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error",
                        "-i", str(mp3_path), "-f", "null", "-"],
                       check=True, stdout=subprocess.DEVNULL)
        rendered.append({**voice, "durationSeconds": round(duration, 3),
                         "sampleRate": sample_rate, "wavSha256": sha256(wav_path),
                         "mp3Sha256": sha256(mp3_path)})
    if len({v["wavSha256"] for v in rendered}) != 3:
        raise SystemExit("The three styles did not produce distinct audio.")
    result = {**plan, "status": "generated-and-decoded", "voices": rendered,
              "assetSha256": asset_hashes, "workflowCommit": os.getenv("GITHUB_SHA"),
              "workflowRunId": os.getenv("GITHUB_RUN_ID")}
    (output / "manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "text.txt").write_text(text + "\n", encoding="utf-8")
    print("Validated three WAV files and three decoded MP3 files.")

if __name__ == "__main__":
    main()
