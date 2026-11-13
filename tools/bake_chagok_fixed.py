#!/usr/bin/env python3
"""One-time Chagok fixed-script bake using Korean Voice's Supertonic 3 F2 voice.

Generate identical audio for all 365 days, without altering production /tts,
R2 cache, or the existing Korean narration pipeline.
"""
import argparse, json, os, re, subprocess, sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "chagok" / "365-fixed-transcript.txt"

def parse():
    items = []
    scene = -1
    for raw in SRC.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if raw.startswith("[") and s.endswith("]"):
            scene += 1
        elif m := re.match(r"^(\d+)\.\s+(.+)$", s):
            items.append({"number": int(m.group(1)), "scene": scene, "en": m.group(2), "ko": ""})
        elif raw.startswith("   ") and s and items and not items[-1]["ko"]:
            items[-1]["ko"] = s
    assert len(items) == 389, f"Incorrect item count: {len(items)}"
    assert max(it["scene"] for it in items) == 16, "Expected 17 ordered scenes"
    assert [it["number"] for it in items] == list(range(1, 390))
    assert all(it["en"] and it["ko"] for it in items), "Missing translation"
    return items

def trim(w, sr):
    w = np.asarray(w, dtype=np.float32).reshape(-1)
    if not len(w): raise ValueError("Empty speech waveform")
    # Preserve initial consonants: only trim long digital silence.
    threshold = max(0.001, float(np.max(np.abs(w))) * 0.012)
    nonzero = np.flatnonzero(np.abs(w) > threshold)
    if len(nonzero):
        pad = int(sr * .035)
        w = w[max(0,int(nonzero[0])-pad):min(len(w),int(nonzero[-1])+pad)]
    f = min(len(w)//2, int(sr*.012))
    if f:
        ramp = np.linspace(0, 1, f, dtype=np.float32)
        w[:f] *= ramp
        w[-f:] *= ramp[::-1]
    peak = max(float(np.max(np.abs(w))), 0.001)
    return w * min(1.0, 0.82/peak)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--scene", type=int, required=True)
    p.add_argument("--assets", default="supertonic3")
    p.add_argument("--out", default="chagok_output")
    p.add_argument("--steps", type=int, default=16)
    args = p.parse_args()
    if not 0 <= args.scene < 17: p.error("--scene must be 0..16")
    data = [x for x in parse() if x["scene"] == args.scene]
    if not data: raise RuntimeError("No items for scene")
    import imageio_ffmpeg
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    assets = Path(args.assets).resolve()
    sys.path.insert(0, str(assets/"py"))
    import helper
    tts = helper.load_text_to_speech(str(assets/"onnx"), False)
    style = helper.load_voice_style([str(assets/"voice_styles"/"F2.json")])
    sr = int(tts.sample_rate)
    def speak(text, lang):
        wave, duration = tts._infer([text], [lang], style, args.steps, 1.03)
        actual = max(1, min(len(np.asarray(wave).reshape(-1)), int(float(np.asarray(duration).reshape(-1)[0])*sr)))
        return trim(np.asarray(wave).reshape(-1)[:actual], sr)
    silence_cache = {}
    def silence(seconds):
        if seconds not in silence_cache:
            silence_cache[seconds] = np.zeros(int(round(seconds*sr)), dtype=np.float32)
        return silence_cache[seconds]
    learn = []
    listen = []
    for i, item in enumerate(data, 1):
        en = speak(item["en"], "en")
        ko = speak(item["ko"], "ko")
        learn += [en, silence(.80), ko, silence(.90), en, silence(1.30)]
        listen += [en, silence(2.40 if item["en"].rstrip().endswith("?") else .86)]
        print(f"scene={args.scene+1}/17 line={i}/{len(data)} global={item['number']} en={len(en)/sr:.2f}s ko={len(ko)/sr:.2f}s", flush=True)
    dest = Path(args.out)
    dest.mkdir(parents=True, exist_ok=True)
    for name, parts in [("learn",learn), ("listen",listen)]:
        audio = np.concatenate(parts)
        output = dest/f"{args.scene:02d}-{name}.mp3"
        cp = subprocess.run([ffmpeg,"-hide_banner","-loglevel","error","-y",
                    "-f","f32le","-ar",str(sr),"-ac","1","-i","pipe:0",
                    "-ar","24000","-ac","1","-codec:a","libmp3lame","-b:a","96k",str(output)],
                    input=audio.tobytes(), capture_output=True)
        if cp.returncode or not output.exists() or output.stat().st_size < 1024:
            raise RuntimeError(f"Failed to encode {output}: {cp.stderr[:500]}")
        print(f"OK {output} bytes={output.stat().st_size} seconds={len(audio)/sr:.2f}",flush=True)
    print(json.dumps({"scene":args.scene,"items":len(data),"first":data[0]["number"],"last":data[-1]["number"]}))
if __name__ == "__main__":
    main()
