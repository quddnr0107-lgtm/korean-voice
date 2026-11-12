"""Repair one approved prosody hotspot without moving paragraph timestamps.

This edits the already selected FP32 voice. It never invokes TTS, changes the
script, shortens voiced audio to meet a length, or modifies source artifacts.
"""
from __future__ import annotations

import argparse
from array import array
import hashlib
import json
import math
from pathlib import Path
import subprocess
import wave


EXPECTED_SOURCE_SHA256 = "b4798c219c7891a8d55dd11baeba15029243220cea2a7aa40b053b6df2145eca"
RATE = 24000
PART_START = 56.22
WINDOW = (1.45, 4.15)
CORE = (1.63, 3.80)
TEMPO = 0.88
FILTER = "atempo=0.88"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as f:
        require((f.getframerate(), f.getnchannels(), f.getsampwidth()) == (RATE, 1, 2),
                f"Unsupported audio format: {path}")
        return f.readframes(f.getnframes())


def write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes(pcm)


def peak(pcm: bytes) -> int:
    return max((abs(x) for x in array("h", pcm)), default=0)


def rms(pcm: bytes) -> float:
    values = array("h", pcm)
    return math.sqrt(sum(x * x for x in values) / len(values))


def sample(t: float) -> int:
    return round(t * RATE)


def encode(source: Path, target: Path, title: str) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(source), "-c:a", "libmp3lame", "-b:a", "128k",
                    "-metadata", f"title={title}", str(target)], check=True)
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                    "-i", str(target), "-f", "null", "-"], check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--delivery", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    require(sha(args.source) == EXPECTED_SOURCE_SHA256, "Unexpected source; review before editing")
    source = read_wav(args.source)
    original_delivery = json.loads(args.delivery.read_text())
    require(original_delivery["timeline"][2]["start_seconds"] == PART_START,
            "Paragraph timeline differs from the reviewed source")
    args.output.mkdir(parents=True, exist_ok=True)
    args.work.mkdir(parents=True, exist_ok=True)
    require(args.output.resolve() != args.source.parent.resolve(),
            "Use a separate output folder to retain the original")

    ws, we = (sample(PART_START + t) for t in WINDOW)
    cs, ce = (sample(PART_START + t) for t in CORE)
    front = source[2 * ws:2 * cs]
    old_tail = source[2 * ce:2 * we]
    require(peak(front) == 0, "The leading allocation contains signal")
    require(peak(old_tail) <= 32, "The trailing allocation may contain speech or breath")
    guard = sample(0.020)
    require(peak(source[2 * (ws - guard):2 * (ws + guard)]) == 0,
            "The first outer edit boundary is not silent")
    require(peak(source[2 * (we - guard):2 * (we + guard)]) == 0,
            "The last outer edit boundary is not silent")

    core_path = args.work / "original_core.wav"
    stretched_path = args.work / "stretched_core.wav"
    write_wav(core_path, source[2 * cs:2 * ce])
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(core_path), "-af", FILTER, "-ar", str(RATE),
                    "-ac", "1", "-c:a", "pcm_s16le", str(stretched_path)], check=True)
    stretched = read_wav(stretched_path)
    original_core_frames = ce - cs
    new_core_frames = len(stretched) // 2
    require(abs(new_core_frames / original_core_frames - 1 / TEMPO) < 0.015,
            "Unexpected stretch ratio; do not trim speech to force a fit")
    remaining_frames = (we - ws) - len(front) // 2 - new_core_frames
    require(remaining_frames >= sample(0.025),
            "Insufficient silent budget; rerender or review, never truncate the voice")
    require(0 < peak(stretched) < 32767, "Empty or clipped processed core")
    rms_change_db = 20 * math.log10(rms(stretched) / rms(source[2 * cs:2 * ce]))
    require(abs(rms_change_db) < 0.5,
            "The stretch changed phrase RMS by at least 0.5 dB; review the processing method")
    require(peak(stretched[:sample(0.005) * 2]) <= 32 and
            peak(stretched[-sample(0.005) * 2:]) <= 32,
            "Processed core has an unsafe edge; retain more padding")

    patch = front + stretched + bytes(remaining_frames * 2)
    require(len(patch) == (we - ws) * 2, "Length mismatch")
    revised = source[:ws * 2] + patch + source[we * 2:]
    require(len(revised) == len(source), "Full duration changed")
    require(revised[:ws * 2] == source[:ws * 2] and revised[we * 2:] == source[we * 2:],
            "Unrequested PCM changes outside the reviewed window")
    require(sha(args.source) == EXPECTED_SOURCE_SHA256, "Source was modified")

    wav_path = args.output / "Bora_03_병역증소지_2분_연결보정.wav"
    mp3_path = wav_path.with_suffix(".mp3")
    write_wav(wav_path, revised)
    encode(wav_path, mp3_path, "병역증 소지의무 — 김보라 ③ 구절 호흡 보정")

    # Same 12-second context before and after, separated by 1.2 seconds of quiet.
    preview_start, preview_end = sample(53.8), sample(65.8)
    comparison = (source[preview_start * 2:preview_end * 2]
                  + bytes(sample(1.2) * 2)
                  + revised[preview_start * 2:preview_end * 2])
    comparison_wav = args.work / "comparison.wav"
    comparison_mp3 = args.output / "Bora_03_58초구간_원본후수정_비교.mp3"
    write_wav(comparison_wav, comparison)
    encode(comparison_wav, comparison_mp3, "첫 12초 원본 · 1.2초 쉬고 수정본")

    changed = [i for i, (a, b) in enumerate(zip(array("h", source[ws * 2:we * 2]),
                                              array("h", patch))) if a != b]
    report = {
        "schema": "bora-local-prosody-repair/v1",
        "source_sha256": EXPECTED_SOURCE_SHA256,
        "source_path": str(args.source),
        "method": "Pitch-preserving time stretch of one continuous phrase; take time from its following quiet interval",
        "new_tts_inference": False,
        "voice_profile": original_delivery["voice_profile"],
        "script_changed": False,
        "sample_rate": RATE,
        "samples": len(revised) // 2,
        "seconds": len(revised) / 2 / RATE,
        "paragraph_timeline_unchanged": True,
        "timeline": original_delivery["timeline"],
        "edit": {
            "part": 2,
            "part_start_seconds": PART_START,
            "window_local_seconds": WINDOW,
            "window_global_seconds": [ws / RATE, we / RATE],
            "core_local_seconds": CORE,
            "ffmpeg_filter": FILTER,
            "tempo_factor": TEMPO,
            "pitch_factor": 1,
            "core_rms_change_db": rms_change_db,
            "old_core_samples": original_core_frames,
            "new_core_samples": new_core_frames,
            "added_speech_window_seconds": (new_core_frames - original_core_frames) / RATE,
            "front_silence_samples_unchanged": len(front) // 2,
            "old_following_padding_samples": len(old_tail) // 2,
            "new_following_padding_samples": remaining_frames,
            "removed_quiet_peak_pcm16": peak(old_tail),
            "actual_changed_sample_span_seconds": [(ws + changed[0]) / RATE,
                                                    (ws + changed[-1] + 1) / RATE],
        },
        "checks": {
            "source_hash_matches": True,
            "source_unchanged": True,
            "all_pcm_outside_edit_window_identical": True,
            "exact_full_sample_count_preserved": True,
            "no_voiced_audio_truncated_to_fit": True,
            "silence_at_outer_edit_boundaries": True,
            "mp3_decode_succeeded": True,
            "core_rms_change_under_half_db": True,
            "peak_pcm16": peak(revised),
            "subjective_listening_verified": False,
        },
        "limitations": [
            "The user reported the problem; the edit is supported by waveform timing, not a completed human listening assessment.",
            "atempo preserves pitch by design; time stretching can still introduce subtle artifacts and has not been perceptually validated here.",
            "Earlier ASR uncertainty in unrelated legal vocabulary is not resolved by this timing-only edit.",
            "The WAV preserves every sample outside the edit window; re-encoded MP3 is not byte-identical outside it.",
            "No site deployment or R2 replacement was performed.",
        ],
        "comparison": {"first": "original", "second": "revised", "each_seconds": 12,
                       "separation_seconds": 1.2, "source_range_seconds": [53.8, 65.8]},
        "files": [{"path": str(p), "bytes": p.stat().st_size, "sha256": sha(p)}
                  for p in [mp3_path, wav_path, comparison_mp3]],
    }
    report_path = args.output / "transition_repair.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"seconds": report["seconds"], "edit": report["edit"],
                      "checks": report["checks"], "files": report["files"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
