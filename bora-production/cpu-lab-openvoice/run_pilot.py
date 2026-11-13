"""Isolated OpenVoice FP32 CPU pilot; no production files or network calls."""
from __future__ import annotations

import hashlib
import json
import math
import platform
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
import scipy
from scipy import fft, signal
import soundfile as sf

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parent
SR = 22050
SEED = 20261113


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def crop_resample(path: Path, seconds: float, out: str):
    y, sr = sf.read(path, dtype="float32", always_2d=True)
    mono = y.mean(axis=1, dtype=np.float32)
    n = round(sr * seconds)
    if n > len(mono):
        raise ValueError("Crop exceeds source")
    mono = mono[:n]
    divisor = math.gcd(sr, SR)
    resampled = signal.resample_poly(mono, SR // divisor, sr // divisor).astype(np.float32)
    sf.write(ROOT / out, resampled, SR, subtype="FLOAT")
    return resampled, {
        "path": str(path.resolve()), "sha256": sha(path),
        "original_sample_rate": sr, "original_samples": len(y),
        "original_channels": y.shape[1], "crop_start_seconds": 0,
        "crop_end_seconds": seconds, "crop_end_original_sample": n,
        "resampled_samples": len(resampled), "resampled_sample_rate": SR,
        "crop_sha256": sha(ROOT / out),
    }


def spectrum(y: np.ndarray):
    # Matches upstream spectrogram_torch: periodic Hann, explicit reflect 384,
    # center=False, unnormalised one-sided FFT and sqrt(Re²+Im²+1e-6).
    y = np.pad(y, (384, 384), mode="reflect")
    frames = np.lib.stride_tricks.sliding_window_view(y, 1024)[::256]
    window = signal.windows.hann(1024, sym=False).astype(np.float32)
    z = fft.rfft(frames * window, axis=1)
    spec = np.sqrt(z.real * z.real + z.imag * z.imag + np.float32(1e-6))
    return np.ascontiguousarray(spec.T[None], dtype=np.float32)


def describe_session(session):
    def one(x):
        return {"name": x.name, "shape": x.shape, "type": x.type}
    return {"inputs": list(map(one, session.get_inputs())),
            "outputs": list(map(one, session.get_outputs())),
            "providers": session.get_providers()}


def main():
    start = time.perf_counter()
    source, source_info = crop_resample(BASE / "sample-raw.wav", 8.76, "source-haeun-8.76s.wav")
    target, target_info = crop_resample(BASE / "bora-gpu-demo.wav", 12.0, "reference-bora-12s.wav")
    src_spec, tgt_spec = spectrum(source), spectrum(target)
    for audio, spec in [(source, src_spec), (target, tgt_spec)]:
        expected_frames = (len(audio) + 768 - 1024) // 256 + 1
        assert spec.shape == (1, 513, expected_frames)
        assert np.isfinite(spec).all() and spec.min() >= 0.001 - 1e-7
    # Independent STFT path: scipy's spectrum scaling is divided by sum(window).
    # Undo it and compare with our direct framed FFT (no added/padded frames).
    padded = np.pad(source, (384, 384), mode="reflect")
    window = signal.windows.hann(1024, sym=False).astype(np.float32)
    _, _, z = signal.stft(padded, fs=SR, window=window, nperseg=1024,
                          noverlap=768, nfft=1024, boundary=None, padded=False)
    direct_check = np.sqrt(np.abs(z * window.sum()) ** 2 + 1e-6)[None]
    stft_max_delta = float(np.max(np.abs(src_spec - direct_check)))
    assert np.allclose(src_spec, direct_check, atol=2e-5, rtol=2e-5)
    preprocess_seconds = time.perf_counter() - start

    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    ort.set_seed(SEED)
    t = time.perf_counter()
    encoder = ort.InferenceSession(str(ROOT / "tone_ref_encoder.onnx"), sess_options=options,
                                   providers=["CPUExecutionProvider"])
    converter = ort.InferenceSession(str(ROOT / "tone_converter.onnx"), sess_options=options,
                                     providers=["CPUExecutionProvider"])
    load_seconds = time.perf_counter() - t

    t = time.perf_counter()
    src_embedding = encoder.run(None, {"spec": np.ascontiguousarray(src_spec.transpose(0, 2, 1))})[0]
    src_embedding_seconds = time.perf_counter() - t
    t = time.perf_counter()
    tgt_embedding = encoder.run(None, {"spec": np.ascontiguousarray(tgt_spec.transpose(0, 2, 1))})[0]
    tgt_embedding_seconds = time.perf_counter() - t
    assert src_embedding.shape == tgt_embedding.shape == (1, 256)
    assert np.isfinite(src_embedding).all() and np.isfinite(tgt_embedding).all()

    feed = {"spec": src_spec,
            "spec_lengths": np.array([src_spec.shape[-1]], dtype=np.int64),
            "src_g": src_embedding[..., None], "tgt_g": tgt_embedding[..., None]}
    print("Running FP32 CPU converter", {k: (v.shape, str(v.dtype)) for k, v in feed.items()}, flush=True)
    t = time.perf_counter()
    result = converter.run(None, feed)[0]
    conversion_seconds = time.perf_counter() - t
    assert result.ndim == 3 and result.shape[:2] == (1, 1)
    output = result[0, 0]
    assert len(output) > 0 and np.isfinite(output).all()
    sf.write(ROOT / "haeun-to-bora-openvoice-fp32.wav", output, SR, subtype="FLOAT")

    report = {
        "status": "forward_completed_content_and_voice_quality_unverified",
        "source": source_info, "reference": target_info,
        "model": {"repo": "TigreGotico/voiceclonnx-openvoice-v2",
                  "revision": json.loads((ROOT / "model-metadata.json").read_text())["sha"],
                  "quantized": False,
                  "files": {n: {"bytes": (ROOT / n).stat().st_size, "sha256": sha(ROOT / n)}
                            for n in ["tone_ref_encoder.onnx", "tone_converter.onnx"]}},
        "runtime": {"onnxruntime": ort.__version__, "numpy": np.__version__,
                    "scipy": scipy.__version__, "python": platform.python_version(),
                    "intra_op_threads": 2, "inter_op_threads": 1,
                    "execution_mode": "sequential", "seed": SEED},
        "graphs": {"encoder": describe_session(encoder), "converter": describe_session(converter)},
        "preprocessing": {"sampling_rate": SR, "n_fft": 1024, "hop": 256, "win": 1024,
                          "window": "periodic_hann", "reflect_pad_each_side": 384,
                          "center": False, "normalised_fft": False, "epsilon": 1e-6,
                          "compression": "none", "source_spec_shape": src_spec.shape,
                          "reference_spec_shape": tgt_spec.shape,
                          "independent_scipy_stft_max_abs_delta": stft_max_delta,
                          "resampling": "scipy.signal.resample_poly default Kaiser(5), constant pad",
                          "deviation_from_upstream": "Upstream librosa resampler unavailable; polyphase resampling used. No direct PyTorch numerical parity validation available.",
                          "reference_extraction": "Single full crop, matching upstream extract_se direct encoder; no VAD segmentation",
                          "output_postprocessing": "none; raw float WAV, no watermark"},
        "timings_seconds": {"preprocess": preprocess_seconds, "session_load": load_seconds,
                            "source_embedding": src_embedding_seconds,
                            "target_embedding": tgt_embedding_seconds,
                            "conversion": conversion_seconds,
                            "embedding_plus_conversion": src_embedding_seconds + tgt_embedding_seconds + conversion_seconds,
                            "total_wall": time.perf_counter() - start},
        "output": {"file": "haeun-to-bora-openvoice-fp32.wav", "sha256": sha(ROOT / "haeun-to-bora-openvoice-fp32.wav"),
                   "shape": result.shape, "sample_rate": SR, "samples": len(output),
                   "seconds": len(output) / SR, "duration_ratio": len(output) / len(source),
                   "peak": float(np.max(np.abs(output))), "rms": float(np.sqrt(np.mean(output ** 2))),
                   "clipping_sample_count": int(np.count_nonzero(np.abs(output) >= 1)),
                   "conversion_real_time_factor": conversion_seconds / (len(source) / SR)},
        "limitations": ["No ASR or human listening performed here", "Model parity reports are exporter claims; not independently reproduced",
                        "No GPU, no INT8, no external upload, no production change"],
    }
    (ROOT / "pilot-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
