"""Read-only measurements of the published RL-027 near the reported 11:45 dropout."""
import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import numpy as np

LESSON = "RL-027"
DESCRIPTOR_DIGEST = "1a8208b34035471e6cf0d96558139301eecf13eb7ba54731e02cb145337e01ed"
RATE = 44100
START, END = 665, 760
ANALYSIS_RATE = 16000
# Timestamps in reports always use the lesson timeline, including at speed 1.2.
WINDOWS = ((665, 686), (686, 705), (705, 719), (719, 740), (740, 760))


def media_bytes(args, data):
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", *args],
        input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        timeout=40, env={"PATH": os.defpath, "LANG": "C"},
    ).stdout


def playback_signal(pcm, speed):
    samples = media_bytes([
        "-f", "s16le", "-ar", str(RATE), "-ac", "1", "-i", "pipe:0",
        "-af", f"atempo={speed}", "-ar", str(ANALYSIS_RATE),
        "-ac", "1", "-f", "f32le", "pipe:1",
    ], pcm)
    return np.frombuffer(samples, dtype="<f4")


def runs(mask, max_bins=None):
    start = None
    for i, enabled in enumerate([*mask, False]):
        if enabled:
            if start is None:
                start = i
            elif max_bins is not None and np.ptp(max_bins[start:i + 1]) > 2:
                yield start, i
                start = i
        elif start is not None:
            yield start, i
            start = None


def measurements(signal, lesson_start, speed):
    # 100 ms frames every 50 ms. A tone must sustain essentially the same
    # spectral peak. RMS and concentration both exclude low-level codec noise.
    width = ANALYSIS_RATE // 10
    hop = width // 2
    frames = np.lib.stride_tricks.sliding_window_view(signal, width)[::hop]
    sample_rms = np.sqrt(np.mean(frames * frames, axis=1))
    magnitude = np.abs(np.fft.rfft(frames * np.hanning(width), axis=1)) ** 2
    magnitude[:, :5] = 0
    peak = magnitude.argmax(axis=1)
    rows = np.arange(len(frames))
    neighborhood = sum(magnitude[rows, np.clip(peak + offset, 0, magnitude.shape[1] - 1)]
                       for offset in (-1, 0, 1))
    concentration = neighborhood / np.maximum(1e-16, magnitude.sum(axis=1))
    spectra = np.sqrt(magnitude)
    spectra /= np.maximum(1e-12, np.linalg.norm(spectra, axis=1))[:, None]
    spectrum_similarity = np.r_[False, np.sum(spectra[1:] * spectra[:-1], axis=1) > .996]

    def intervals(mask, threshold, bins=None):
        result = []
        for a, b in runs(mask, bins):
            if (b - a + 1) * hop / ANALYSIS_RATE < threshold:
                continue
            start = lesson_start + a * hop * speed / ANALYSIS_RATE
            finish = lesson_start + ((b - 1) * hop + width) * speed / ANALYSIS_RATE
            item = {"from_s": round(start, 2), "to_s": round(finish, 2)}
            if bins is not None:
                item["dominant_hz"] = int(np.median(bins[a:b]) * ANALYSIS_RATE / width)
            result.append(item)
        return result
    return {
        "rms_dbfs": round(float(20 * np.log10(max(1e-12, np.sqrt(np.mean(signal ** 2))))), 1),
        "quiet_runs": intervals(sample_rms < 10 ** (-48 / 20), 0.5),
        "tone_runs": intervals((sample_rms > .005) & (concentration > .72), .45, peak),
        "stationary_spectrum_runs": intervals((sample_rms > .005) & spectrum_similarity, .65, peak),
    }


def recover(directory, key):
    import delivery
    import lecture_crypto as crypto
    import lecture_pack as pack

    raw = (directory / f"{LESSON}.descriptor.json").read_bytes()
    if crypto.digest(raw) != DESCRIPTOR_DIGEST:
        raise ValueError("unexpected RL-027 release descriptor")
    descriptor = delivery.check_descriptor(json.loads(raw), LESSON)
    audio = descriptor["audio"]
    if audio["sample_rate"] != RATE or audio["lesson_id"] != LESSON:
        raise ValueError("unexpected audio format")
    filename = directory / delivery.media_name(LESSON, "audio", audio)
    with filename.open("rb") as encrypted:
        if filename.stat().st_size != audio["pack_bytes"]:
            raise ValueError("wrong audio size")
        recovered = bytearray()
        decoded_parts = []
        for part in audio["parts"]:
            if part["end_sample"] <= START * RATE or part["start_sample"] >= END * RATE:
                continue
            encrypted.seek(part["pack_offset"])
            data = pack.open_part(
                key, part, encrypted.read(part["cipher_bytes"]), expected_id=LESSON,
                expected_part_index=part["part_index"], expected_pack_id=audio["object_id"])
            # A seekable MP3 input lets ffmpeg trim the encoded end padding.
            with os.fdopen(os.memfd_create("audio-part"), "w+b") as stream:
                stream.write(data)
                stream.seek(0)
                proc = subprocess.run(
                    ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-i",
                     f"/proc/self/fd/{stream.fileno()}", "-ac", "1", "-ar", str(RATE),
                     "-f", "s16le", "pipe:1"], pass_fds=(stream.fileno(),),
                    capture_output=True, check=True, timeout=40,
                    env={"PATH": os.defpath, "LANG": "C"})
            pcm = proc.stdout
            duration = part["end_sample"] - part["start_sample"]
            decoded_parts.append({"part": part["part_index"],
                                  "end_s": round(part["end_sample"] / RATE, 3),
                                  "decoded_sample_delta": len(pcm) // 2 - duration})
            if abs(len(pcm) // 2 - duration) > 1152:
                raise ValueError("decoded part duration mismatch")
            left = max(START * RATE - part["start_sample"], 0)
            right = min(END * RATE - part["start_sample"], duration)
            recovered.extend(pcm[2 * left:2 * right].ljust(2 * (right-left), b"\x00"))
    if len(recovered) != 2 * (END - START) * RATE:
        raise ValueError("incomplete sample coverage")
    metadata = descriptor["content"]
    data = (directory / delivery.media_name(LESSON, "content", metadata)).read_bytes()
    content, _ = crypto.open_range(key, metadata, data, expected_id=LESSON,
                                   expected_kind="private-metadata", expected_key_id=delivery.KEY_ID)
    record = json.loads(content)
    if record["id"] != LESSON or record["audio_pack_sha256"] != audio["pack_sha256"]:
        raise ValueError("wrong timed manuscript")
    return bytes(recovered), record["segments"], decoded_parts


def recognize(signal, segments, model_path):
    from difflib import SequenceMatcher
    import re
    from faster_whisper import WhisperModel
    # Use the same digest-verified offline model as the original production QA.
    sys.path.append(str(Path(__file__).resolve().parents[2] / "bora-production"))
    import tb001_asr_crosscheck as asr
    asr.verify_model(model_path)
    model = WhisperModel(str(model_path), device="cpu", compute_type="int8",
                         cpu_threads=4, local_files_only=True)
    def normalize(text):
        return re.sub(r"[^가-힣a-zA-Z0-9]", "", text).lower()
    # Segments are independently transcribed. Return lengths and similarity
    # only. Korean number readings and legal paragraph glyphs may differ.
    results = []
    for i, segment in enumerate(segments):
        begin, end = segment["start_s"], segment["end_s"]
        if begin < START or end > END or end <= begin:
            continue
        section = signal[round((begin - START) * ANALYSIS_RATE):round((end - START) * ANALYSIS_RATE)]
        if not len(section):
            continue
        hypothesis, _ = model.transcribe(section, **asr.TRANSCRIBE)
        observed = normalize("".join(s.text for s in hypothesis))
        expected = normalize(segment["text"])
        results.append({"segment": i, "from_s": round(begin, 2), "to_s": round(end, 2),
                        "expected_chars": len(expected), "recognized_chars": len(observed),
                        "text_similarity": round(SequenceMatcher(None, expected, observed,
                                                                 autojunk=False).ratio(), 3)})
    return results


def main():
    from namespace_guard import validate_snapshot
    directory, uid, host_namespace, model_dir = sys.argv[1:]
    validate_snapshot(os.getuid(), int(uid), socket.if_nameindex(),
                      Path("/proc/net/route").read_text(), os.readlink("/proc/self/ns/net"),
                      host_namespace, Path("/proc/net/ipv6_route").read_text())
    key = base64.b64decode(os.environ.pop("ADMIN_LAW_PREMIUM_MEDIA_KEY_B64"), validate=True)
    pcm, segments, decoded = recover(Path(directory), key)
    del key
    print(json.dumps({"decoded_parts": decoded}), flush=True)
    one = playback_signal(pcm, 1.0)
    for speed, signal in ((1.0, one), (1.2, playback_signal(pcm, 1.2))):
        for left, right in WINDOWS:
            a = round((left - START) * ANALYSIS_RATE / speed)
            b = round((right - START) * ANALYSIS_RATE / speed)
            print(json.dumps({"speed": speed, "window": [left, right],
                              **measurements(signal[a:b], left, speed)}), flush=True)
    if model_dir != "unavailable":
        for result in recognize(one, segments, Path(model_dir)):
            print(json.dumps({"asr": result}), flush=True)
    else:
        print("Speech recognition unavailable; acoustic measurements only.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Protected material is never formatted into the public run log.
        print("RL-027 quality check failed:", type(error).__name__, flush=True)
        raise SystemExit(1) from None
