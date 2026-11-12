"""One bounded public minute-listening profile per isolated matrix job."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

from spec import (INSTRUCTION, MAX_TOKENS, MODEL_REPOSITORY, MODEL_REVISION,
                  NATIVE_COMMIT, PACKAGES, PROFILE_SECONDS, PROFILES, PUBLIC_TEXT, SEED)

HERE = Path(__file__).resolve().parent


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def command(profile, model, native, output):
    if profile["engine"] == "python":
        return [sys.executable, str(HERE / "python_worker.py"), "--model", str(model),
                "--output", str(output), "--dtype", profile["dtype"],
                "--threads", str(profile["threads"])]
    result = [str(native), "-d", str(model), "-t", PUBLIC_TEXT,
              "-o", str(output / "audio.wav"), "-s", "sohee", "-l", "Korean",
              "--instruct", INSTRUCTION, "--threads", str(profile["threads"]),
              "--eos-strategy", "off", "--no-compose", "--temperature", "0.8",
              "--top-p", "0.9", "--top-k", "50", "--rep-penalty", "1.05",
              "--seed", str(SEED), "--max-tokens", str(MAX_TOKENS)]
    if profile["dtype"] == "int8":
        result.append("--int8")
    return result


def process_environment(profile, output):
    env = os.environ.copy()
    # No inherited engine tuning or implicit model credentials can enter the screen.
    for name in list(env):
        if name.startswith("QWEN_") or name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
            env.pop(name)
    count = str(profile["threads"])
    env.update(OMP_NUM_THREADS=count, MKL_NUM_THREADS=count, OPENBLAS_NUM_THREADS=count,
               NUMEXPR_NUM_THREADS=count, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
               HF_HUB_DISABLE_IMPLICIT_TOKEN="1", HF_HUB_DISABLE_TELEMETRY="1",
               TOKENIZERS_PARALLELISM="false", PYTHONUNBUFFERED="1")
    if profile["engine"] == "native":
        env.update(BORA_BENCH_METRICS=str(output / "worker.json"),
                   QWEN_DISPATCH_JSON=str(output / "dispatch.json"),
                   QWEN_TTS_STREAM_LAYOUT="0")
    return env


def bounded_process(argv, directory, env, limit):
    started = time.monotonic()
    terminated = False
    sent_term = None
    with (directory / "stdout.log").open("wb") as out, (directory / "stderr.log").open("wb") as err:
        proc = subprocess.Popen(argv, stdout=out, stderr=err, env=env, start_new_session=True)
        while True:
            pid, status, usage = os.wait4(proc.pid, os.WNOHANG)
            if pid:
                proc.returncode = os.waitstatus_to_exitcode(status)
                break
            elapsed = time.monotonic() - started
            if elapsed >= limit and not terminated:
                terminated, sent_term = True, time.monotonic()
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            if sent_term is not None and time.monotonic() - sent_term >= 1.0:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            time.sleep(0.1)
    return {"pid": proc.pid, "exit_code": proc.returncode, "timed_out": terminated,
            "process_wall_seconds": time.monotonic() - started,
            "max_rss_kib": usage.ru_maxrss, "user_cpu_seconds": usage.ru_utime,
            "system_cpu_seconds": usage.ru_stime, "wall_limit_seconds": limit}


def hardware():
    info = {"platform": list(os.uname()[:1]) + list(os.uname()[2:]),
            "logical_cpus": os.cpu_count(), "cpu_affinity": sorted(os.sched_getaffinity(0)),
            "runner_image": {k: os.environ.get(k) for k in ("ImageOS", "ImageVersion")}}
    raw = Path("/proc/cpuinfo").read_text()
    first = raw.split("\n\n", 1)[0]
    info["cpu"] = dict(line.split(":", 1) for line in first.splitlines() if ":" in line)
    info["cpu"] = {k.strip(): v.strip() for k, v in info["cpu"].items()
                   if k.strip() in ("vendor_id", "cpu family", "model", "model name", "stepping", "flags")}
    info["lscpu"] = json.loads(subprocess.check_output(["lscpu", "--json"], text=True))
    for name in ("cpu.max", "memory.max"):
        path = Path("/sys/fs/cgroup") / name
        info["cgroup_" + name] = path.read_text().strip() if path.exists() else None
    return info


def classify(profile, folder, result):
    worker_path = folder / "worker.json"
    worker = json.loads(worker_path.read_text()) if worker_path.exists() else {}
    result["worker"] = worker
    result["status"] = "timeout" if result["timed_out"] else "failed"
    result["eligible_for_speed_comparison"] = False
    for key in ("cold_model_load_seconds", "tts_seconds"):
        if isinstance(worker.get(key), (int, float)) and worker[key] >= 0:
            result[key] = worker[key]
    if profile["engine"] == "native":
        log = (folder / "stderr.log").read_text(errors="replace")
        eos = re.search(r"EOS at frame (\d+)", log)
        frames = worker.get("generated_frames", MAX_TOKENS)
        natural = eos is not None and int(eos.group(1)) == frames and frames < MAX_TOKENS
        result.update(natural_eos=natural, cap_reached=frames >= MAX_TOKENS,
                      flags_in_actual_process="[FLAGS]" in log,
                      dispatch_in_actual_process=(folder / "dispatch.json").is_file())
    else:
        natural = worker.get("natural_eos") is True
        result.update(natural_eos=natural, cap_reached=worker.get("cap_reached"))
    wave = folder / "audio.wav"
    if wave.exists():
        import numpy as np
        import soundfile as sf
        audio, rate = sf.read(wave, dtype="float32", always_2d=True)
        valid = len(audio) > 0 and rate == 24000 and audio.shape[1] == 1
        valid = bool(valid and np.isfinite(audio).all() and np.max(np.abs(audio)) > 1e-7)
        result.update(audio_seconds=len(audio) / rate, audio_samples=len(audio),
                      audio_sample_rate=rate, audio_sha256=digest(wave),
                      basic_signal_valid=valid)
        if not result["timed_out"] and result["exit_code"] == 0 and natural and valid:
            result["status"] = "complete_unreviewed"
            result["eligible_for_speed_comparison"] = True
            seconds = result["audio_seconds"]
            result["tts_rtf"] = result["tts_seconds"] / seconds
            result["load_plus_tts_rtf"] = (result["cold_model_load_seconds"] + result["tts_seconds"]) / seconds
            result["process_rtf"] = result["process_wall_seconds"] / seconds
        elif not result["timed_out"] and (result["cap_reached"] or not natural):
            result["status"] = "failed_truncated_or_unverified_eos"
    result["quality_qualified"] = False
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--only-profile", choices=tuple(p["id"] for p in PROFILES), required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    args.model, args.native, args.output = (p.resolve() for p in (args.model, args.native, args.output))
    selected = [p for p in PROFILES if p["id"] == args.only_profile]
    plan = [{**p, "command": command(p, args.model, args.native, args.output / p["id"])} for p in selected]
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return
    forbidden_names = {"ADMIN_LAW_PREMIUM_SOURCE_KEY_B64", "ADMIN_LAW_PREMIUM_MEDIA_KEY_B64"}
    if forbidden_names.intersection(os.environ):
        raise RuntimeError("Public benchmark must not receive protected key variables")
    versions = {name: importlib.metadata.version(name) for name in PACKAGES}
    if versions != PACKAGES:
        raise RuntimeError("Installed direct package pins differ from the public runtime")
    args.output.mkdir(parents=True, exist_ok=True)
    deadline = float(os.environ.get("BORA_BENCH_DEADLINE", time.time() + PROFILE_SECONDS + 60))
    manifest = {
        "schema": "bora-public-minute-listening/v1", "utc_start": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "hardware": hardware(), "packages": versions, "public_text": PUBLIC_TEXT,
        "public_text_characters": len(PUBLIC_TEXT), "instruction": INSTRUCTION,
        "model_repository": MODEL_REPOSITORY, "model_revision": MODEL_REVISION,
        "native_source_commit": NATIVE_COMMIT, "native_binary_sha256": digest(args.native),
        "harness_sha256": {p.name: digest(p) for p in sorted(HERE.iterdir()) if p.is_file()},
        "seed": SEED, "max_tokens": MAX_TOKENS, "temperature": 0.8, "top_p": 0.9,
        "top_k": 50, "repetition_penalty": 1.05, "trials_per_profile": 1,
        "order": [p["id"] for p in selected], "same_host_sequential": False,
        "only_profile": args.only_profile, "separate_matrix_runners": True,
        "comparison_purpose": "Same-input listening samples; runner CPU types can differ.",
        "public_text_sha256": hashlib.sha256(PUBLIC_TEXT.encode("utf-8")).hexdigest(),
        "cold_definition": "Fresh process; filesystem page cache is not cleared. Download may warm it.",
        "sampling_difference": "Native code predictor uses argmax; Python retains original subtalker sampling (.9/1.0/50).",
        "quality_qualified": False, "results": [],
    }
    write_json(args.output / "run_manifest.json", manifest)
    for profile in plan:
        directory = args.output / profile["id"]
        directory.mkdir(exist_ok=True)
        remaining = deadline - time.time() - 35  # preserve time to finalize and upload
        result = {k: v for k, v in profile.items()}
        result["cpu_identity_same_as_run_manifest"] = True
        if remaining < 10:
            result.update(status="not_run_budget_exhausted", eligible_for_speed_comparison=False)
        else:
            limit = min(PROFILE_SECONDS, remaining)
            print(f"START {profile['id']} (wall cap {limit:.0f}s)", flush=True)
            env = process_environment(profile, directory)
            result["engine_environment"] = {k: v for k, v in env.items()
                                            if k.startswith("QWEN_") or k in (
                                                "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                                                "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")}
            try:
                result.update(bounded_process(profile["command"], directory, env, limit))
                result = classify(profile, directory, result)
            except Exception as exc:
                result.update(status="failed_harness_or_engine", error=type(exc).__name__ + ": " + str(exc),
                              eligible_for_speed_comparison=False)
            print(f"END {profile['id']}: {result['status']}", flush=True)
        write_json(directory / "metrics.json", result)
        manifest["results"].append(result)
        write_json(args.output / "run_manifest.json", manifest)
    lines = ["# Public Korean TTS minute listening sample", "",
             "One trial per profile. Quality has not been qualified. Cap hits/timeouts are failures.", "",
             "| Profile | Status | Load s | TTS s | Audio s | TTS RTF | Max RSS MiB |", "|---|---|---:|---:|---:|---:|---:|"]
    for item in manifest["results"]:
        def fmt(key):
            value = item.get(key)
            return f"{value:.3f}" if isinstance(value, (int, float)) else "—"
        rss = item.get("max_rss_kib")
        memory = f"{rss / 1024:.1f}" if rss is not None else "—"
        lines.append(f"| {item['id']} | {item['status']} | {fmt('cold_model_load_seconds')} | {fmt('tts_seconds')} | {fmt('audio_seconds')} | {fmt('tts_rtf')} | {memory} |")
    lines += ["", "Each profile runs on a separate matrix runner; compare recorded CPU identities before timing claims.",
              "Fresh process does not mean a cold filesystem cache.",
              "Native code predictor uses argmax; Python retains the original sampled code predictor.",
              "Audio durations can differ. RTF divides by each output's measured sample duration."]
    summary = "\n".join(lines) + "\n"
    (args.output / "summary.md").write_text(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
            stream.write(summary)
    print(summary)
    if not all(r.get("eligible_for_speed_comparison") for r in manifest["results"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
