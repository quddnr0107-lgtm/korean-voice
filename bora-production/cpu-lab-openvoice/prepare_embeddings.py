"""Recreate the pilot's tone vectors without overwriting its timing receipts.

Uses existing local files only; no download, TTS, conversion, or installation.

Example:
    .voice-venv/bin/python voice-sample/openvoice-cpu-pilot/prepare_embeddings.py \
        --output voice-sample/openvoice-cpu-pilot/reproduced-embedding-cache

The output defaults to that separate subdirectory. Existing output files are
never overwritten. run_pipeline.py originally used the preserved vectors in
the pilot directory; compare this reproduction separately before using it.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
import soundfile as sf

from run_pilot import sha, spectrum


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot-dir", type=Path, default=Path(__file__).resolve().parent,
                        help="Directory containing existing WAVs and tone_ref_encoder.onnx")
    parser.add_argument("--output", type=Path,
                        help="Separate output directory; default: PILOT_DIR/reproduced-embedding-cache")
    args = parser.parse_args()
    pilot = args.pilot_dir.resolve()
    output = (args.output or pilot / "reproduced-embedding-cache").resolve()
    if output == pilot:
        parser.error("Use a separate --output directory to preserve the original cache and timing report.")
    output_names = ["source-embedding.npy", "target-embedding.npy", "embedding-cache-report.json"]
    conflicts = [str(output / name) for name in output_names if (output / name).exists()]
    if conflicts:
        parser.error("Existing output files will not be overwritten: " + ", ".join(conflicts))
    encoder_path = pilot / "tone_ref_encoder.onnx"
    inputs = [("source", "source-haeun-8.76s.wav"), ("target", "reference-bora-12s.wav")]
    for path in [encoder_path] + [pilot / name for _, name in inputs]:
        if not path.is_file():
            parser.error(f"Required local file is missing: {path}")
    output.mkdir(parents=True, exist_ok=True)

    start = time.perf_counter()
    ort.disable_telemetry_events()
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 2
    opts.inter_op_num_threads = 1
    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    t = time.perf_counter()
    encoder = ort.InferenceSession(str(encoder_path), sess_options=opts,
                                   providers=["CPUExecutionProvider"])
    load_seconds = time.perf_counter() - t
    records = {}
    for label, name in inputs:
        wave_path = pilot / name
        wave, sr = sf.read(wave_path, dtype="float32")
        if sr != 22050 or wave.ndim != 1 or not len(wave) or not np.isfinite(wave).all():
            raise ValueError(f"Expected a finite, nonempty mono 22050 Hz WAV: {wave_path}")
        t = time.perf_counter()
        spec = spectrum(wave)
        embedding = encoder.run(None, {"spec": np.ascontiguousarray(spec.transpose(0, 2, 1))})[0]
        if embedding.shape != (1, 256) or embedding.dtype != np.float32 or not np.isfinite(embedding).all():
            raise ValueError(f"Unexpected reference encoder output for {label}")
        embedding = embedding[..., None]
        embedding_path = output / f"{label}-embedding.npy"
        with embedding_path.open("xb") as handle:
            np.save(handle, embedding, allow_pickle=False)
        extract_save_seconds = time.perf_counter() - t
        records[label] = {
            "wave": name, "wave_sha256": sha(wave_path),
            "sample_rate": sr, "samples": len(wave), "seconds": len(wave) / sr,
            "embedding_sha256": sha(embedding_path), "embedding_shape": list(embedding.shape),
            "preprocess_extract_save_seconds": extract_save_seconds,
        }
    report = {
        "encoder_load_seconds": load_seconds,
        "cache_creation_total_seconds": time.perf_counter() - start,
        "records": records,
        "purpose": "Reproduction from the prior pilot crops; original caches and timing receipts preserved",
        "encoder": {"file": str(encoder_path), "sha256": sha(encoder_path),
                    "onnxruntime": ort.__version__, "quantized": False,
                    "providers": encoder.get_providers(), "intra_op_threads": 2, "inter_op_threads": 1},
        "preprocessing": "run_pilot.spectrum: FFT1024/hop256/periodic Hann/reflect384/centerFalse/sqrt(Re²+Im²+1e-6), no resampling or compression",
        "output_directory": str(output),
        "limitations": ["No TTS, voice conversion, ASR or listening performed",
                        "New timings belong to this reproduction and do not replace the original measured timings"],
    }
    with (output / "embedding-cache-report.json").open("x") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
