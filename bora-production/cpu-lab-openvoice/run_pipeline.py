"""One measured CPU run: fresh Supertonic F2 synthesis -> cached-tone OpenVoice."""
from __future__ import annotations

import os
os.environ["ORT_DISABLE_TELEMETRY"] = "1"
os.environ["OMP_NUM_THREADS"] = "2"
os.environ["OPENBLAS_NUM_THREADS"] = "2"

import json
import platform
import sys
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
import soundfile as sf
from scipy import signal

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parent
sys.path.insert(0, str(BASE / "assets/py"))
import helper
from run_pilot import sha, spectrum, describe_session

LINES = [
    ("통합방위법을 공부할 때, 처음부터 모든 문장을 외우려고 하면 금방 지칩니다.", .95, .55),
    ("먼저, 큰 그림을 떠올려 보세요.", .93, .65),
]


def cpu_info():
    info = {}
    for line in Path("/proc/cpuinfo").read_text().splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            if key.strip() in ("model name", "vendor_id", "cpu MHz", "flags"):
                info.setdefault(key.strip(), value.strip())
    info["logical_cpus_visible"] = os.cpu_count()
    if hasattr(os, "sched_getaffinity"):
        info["affinity"] = sorted(os.sched_getaffinity(0))
    for path in ["/sys/fs/cgroup/cpu.max", "/sys/fs/cgroup/memory.max"]:
        try:
            info[path] = Path(path).read_text().strip()
        except OSError:
            info[path] = "unavailable"
    return info


def main():
    ort.disable_telemetry_events()
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 2
    opts.inter_op_num_threads = 1
    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    ort.set_seed(20261113)

    # End-to-end timer starts before model and cached-vector loading. Imports,
    # downloads, hash generation and one-time embedding preparation are excluded.
    start = time.perf_counter()
    t = time.perf_counter()
    model = BASE / "assets/onnx"
    tts = helper.TextToSpeech(helper.load_cfgs(str(model)),
                             helper.load_text_processor(str(model)),
                             *helper.load_onnx_all(str(model), opts, ["CPUExecutionProvider"]))
    style = helper.load_voice_style([str(BASE / "assets/voice_styles/F2.json")])
    base_load = time.perf_counter() - t
    sr = tts.sample_rate
    assert sr == 44100
    t = time.perf_counter()
    converter = ort.InferenceSession(str(ROOT / "tone_converter.onnx"),
                                     sess_options=opts, providers=["CPUExecutionProvider"])
    src_g = np.load(ROOT / "source-embedding.npy", allow_pickle=False)
    tgt_g = np.load(ROOT / "target-embedding.npy", allow_pickle=False)
    assert src_g.shape == tgt_g.shape == (1, 256, 1)
    assert src_g.dtype == tgt_g.dtype == np.float32
    converter_load = time.perf_counter() - t
    pieces, records = [], []
    base_start = time.perf_counter()
    for i, (text, speed, pause) in enumerate(LINES):
        segment_start = time.perf_counter()
        np.random.seed(20261113 + i)
        t = time.perf_counter()
        wave, duration = tts._infer([text], ["ko"], style, 16, speed)
        infer_seconds = time.perf_counter() - t
        wave = np.asarray(wave, dtype=np.float32).reshape(-1)
        predicted_seconds = float(np.asarray(duration).reshape(-1)[0])
        wave = wave[:round(predicted_seconds * sr)]
        assert len(wave) and np.isfinite(wave).all()
        original_samples = len(wave)
        frame = round(.01 * sr)
        n = len(wave) // frame
        rms = np.sqrt(np.mean(wave[:n * frame].reshape(n, frame) ** 2, axis=1))
        live = np.flatnonzero(rms > 10 ** (-48 / 20))
        assert len(live)
        left = max(0, round(live[0] * frame - .08 * sr))
        right = min(len(wave), round((live[-1] + 1) * frame + .16 * sr))
        wave = wave[left:right]
        k = min(round(.008 * sr), len(wave) // 2)
        wave[:k] *= np.linspace(0, 1, k)
        wave[-k:] *= np.linspace(1, 0, k)
        pieces.extend([wave, np.zeros(round(pause * sr), dtype=np.float32)])
        records.append({"index": i, "text": text, "seed": 20261113 + i,
                        "speed": speed, "pause_seconds": pause,
                        "raw_predicted_seconds": predicted_seconds,
                        "pretrim_samples": original_samples,
                        "trim_start_sample": left, "trim_end_sample": right,
                        "actual_samples": len(wave), "actual_seconds": len(wave) / sr,
                        "tts_inference_seconds": infer_seconds,
                        "segment_including_trim_fade_seconds": time.perf_counter() - segment_start})
        print(json.dumps(records[-1], ensure_ascii=False), flush=True)
    source = np.concatenate(pieces)
    gain = .86 / max(float(np.abs(source).max()), 1e-6)
    source *= gain
    source_path = ROOT / "pipeline-haeun-source.wav"
    sf.write(source_path, source, sr, subtype="PCM_16")
    base_render = time.perf_counter() - base_start

    t = time.perf_counter()
    source_pcm, sr = sf.read(source_path, dtype="float32")
    source_22050 = signal.resample_poly(source_pcm, 1, 2).astype(np.float32)
    spec = spectrum(source_22050)
    assert np.isfinite(spec).all()
    preprocess = time.perf_counter() - t
    feed = {"spec": spec, "spec_lengths": np.array([spec.shape[-1]], dtype=np.int64),
            "src_g": src_g, "tgt_g": tgt_g}
    t = time.perf_counter()
    result = converter.run(None, feed)[0]
    convert = time.perf_counter() - t
    assert result.ndim == 3 and result.shape[:2] == (1, 1)
    output = result[0, 0]
    assert output.size and np.isfinite(output).all()
    t = time.perf_counter()
    output_path = ROOT / "pipeline-haeun-to-bora.wav"
    sf.write(output_path, output, 22050, subtype="FLOAT")
    output_write = time.perf_counter() - t
    end_to_end = time.perf_counter() - start

    # Reproducibility metadata after stopping benchmark clock.
    paths = [BASE / "render.py", BASE / "assets/py/helper.py",
             BASE / "assets/voice_styles/F2.json", ROOT / "run_pipeline.py",
             ROOT / "run_pilot.py", ROOT / "source-embedding.npy", ROOT / "target-embedding.npy",
             ROOT / "tone_converter.onnx", ROOT / "tone_ref_encoder.onnx",
             source_path, output_path]
    paths += sorted(model.glob("*"))
    report = {
        "status": "single_forward_completed_content_and_voice_quality_unverified",
        "pipeline": "Fresh Supertonic3 F2 first-two-sentence synthesis -> OpenVoiceV2 FP32 cached tone embeddings",
        "timing_scope": "Same process after imports: model initialization + cached-vector load + fresh TTS + identical trim/fade/pauses + PCM16 write/read + resample/STFT + conversion + raw float WAV save. No ASR, download, hash calculation or import time.",
        "hardware": cpu_info(),
        "runtime": {"python": platform.python_version(), "onnxruntime": ort.__version__,
                    "numpy": np.__version__, "providers": ["CPUExecutionProvider"],
                    "intra_op_threads": 2, "inter_op_threads": 1, "execution_mode": "sequential",
                    "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2", "quantized": False},
        "base_tts": {"engine": "Supertonic3", "voice": "F2", "steps": 16,
                     "sample_rate": sr, "segments": records,
                     "trim": "10ms RMS > -48dB; retain 80ms leading/160ms trailing",
                     "fade_milliseconds": 8, "normalization_peak": .86,
                     "normalization_gain": gain,
                     "normalization_scope": "These two newly generated sentences; original sample-raw.wav normalization covered all nine sentences",
                     "actual_voice_seconds_excluding_inserted_pauses": sum(r["actual_seconds"] for r in records),
                     "inserted_pause_seconds": sum(r["pause_seconds"] for r in records),
                     "source_total_seconds": len(source) / sr},
        "openvoice": {"repo": "TigreGotico/voiceclonnx-openvoice-v2",
                      "revision": json.loads((ROOT / "model-metadata.json").read_text())["sha"],
                      "graph": describe_session(converter), "input_spec_shape": list(spec.shape),
                      "embedding_reuse": json.loads((ROOT / "embedding-cache-report.json").read_text()),
                      "resampling": "scipy polyphase 44100->22050; differs from unavailable upstream librosa resampler",
                      "STFT": "Official-equivalent algorithm: 1024 FFT, hop256, periodic Hann, reflect384, centerFalse, sqrt(Re²+Im²+1e-6), no log",
                      "postprocessing": "none, raw float WAV; no watermark"},
        "timings_seconds": {"base_model_load": base_load,
                            "converter_and_embedding_cache_load": converter_load,
                            "models_and_cache_load_total": base_load + converter_load,
                            "base_tts_inference_total": sum(r["tts_inference_seconds"] for r in records),
                            "base_render_including_trim_fade_normalize_and_write": base_render,
                            "conversion_preprocess": preprocess, "conversion_forward": convert,
                            "conversion_output_write": output_write,
                            "base_render_plus_conversion_forward": base_render + convert,
                            "cached_models_processing_total": base_render + preprocess + convert + output_write,
                            "end_to_end_including_model_load": end_to_end},
        "output": {"file": output_path.name, "sample_rate": 22050,
                   "shape": list(result.shape), "seconds": len(output) / 22050,
                   "duration_ratio_to_source": (len(output) / 22050) / (len(source) / sr),
                   "peak": float(np.max(np.abs(output))), "rms": float(np.sqrt(np.mean(output ** 2))),
                   "clipping_samples": int(np.count_nonzero(np.abs(output) >= 1)),
                   "end_to_end_real_time_factor": end_to_end / (len(output) / 22050),
                   "cached_models_processing_real_time_factor": (base_render + preprocess + convert + output_write) / (len(output) / 22050)},
        "files": {str(p.relative_to(BASE)): {"bytes": p.stat().st_size, "sha256": sha(p)} for p in paths},
        "limitations": ["One measurement, shared host, filesystem cache state uncontrolled",
                        "No ASR or listening assessment in this script", "No production changes, installs or external voice upload",
                        "The two embedding vectors were prepared separately from previous pilot crops; first-use cache creation cost is explicitly recorded, excluded from cached-vector end-to-end timing",
                        "No direct PyTorch preprocessing parity test; exporter parity not independently reproduced"],
    }
    (ROOT / "pipeline-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"timings_seconds": report["timings_seconds"], "base_tts": report["base_tts"], "output": report["output"]}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
