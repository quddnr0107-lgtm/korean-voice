"""Read-only ASR verification for one fixed public synthesis run."""
import argparse
import ast
import difflib
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request

SOURCE_RUN = "43177833903"
MODEL_REPOSITORY = "Systran/faster-whisper-small"
MODEL_REVISION = "536b0662742c02347bc0e980a01041f333bce120"
MODEL_FILES = ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")
HERE = Path(__file__).resolve().parent
ARTICLE = re.compile(r"제\s*(?:83|팔십삼)\s*조\s*의\s*(?:3|삼)(?![0-9십백천만])")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def canonical_article(text):
    # Do not rewrite legal vocabulary, word endings, other numbers, or punctuation.
    return ARTICLE.sub("제83조의3", text)


def edit_distance(reference, hypothesis):
    row = list(range(len(hypothesis) + 1))
    for i, left in enumerate(reference, 1):
        next_row = [i]
        for j, right in enumerate(hypothesis, 1):
            next_row.append(min(row[j] + 1, next_row[j - 1] + 1,
                                row[j - 1] + (left != right)))
        row = next_row
    return row[-1]


def comparison(reference, transcript):
    left, right = canonical_article(reference), canonical_article(transcript)
    edits = []
    for tag, i, j, a, b in difflib.SequenceMatcher(None, left, right, autojunk=False).get_opcodes():
        if tag != "equal":
            edits.append({"operation": tag, "reference_span": [i, j],
                          "transcript_span": [a, b], "expected_fragment": left[i:j],
                          "heard_fragment": right[a:b]})
    distance = edit_distance(left, right)
    return {"exact_match_after_article_canonicalization": left == right,
            "character_edit_distance": distance,
            "character_error_rate": distance / max(1, len(left)),
            "cer_includes_spacing_and_punctuation": True,
            "normalization": "Only 제83조의3 / 제팔십삼조의 삼 number forms are canonicalized; no other word or punctuation correction.",
            "differences": edits}


def wait_for_source(output):
    repository = os.environ["GITHUB_REPOSITORY"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise RuntimeError("Unexpected repository identifier")
    # The repository is the current workflow's repository; the source run is immutable.
    url = f"https://api.github.com/repos/{repository}/actions/runs/{SOURCE_RUN}"
    deadline = min(time.time() + 600,
                   float(os.environ.get("BORA_VERIFY_DEADLINE", time.time() + 840)) - 180)
    report = {"source_run": SOURCE_RUN, "repository": repository,
              "poll_interval_seconds": 20, "maximum_wait_seconds": 600, "polls": []}
    while time.time() < deadline:
        request = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + os.environ["GH_TOKEN"],
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "bora-public-audio-verifier",
        })
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                run = json.load(response)
        except urllib.error.HTTPError as error:
            # Never print request headers, credentials, or the error response body.
            report["error"] = {"kind": "github_http_error", "status": error.code}
            write_json(output / "source-run-poll.json", report)
            raise SystemExit(f"GitHub read-only status request failed: HTTP {error.code}")
        if str(run.get("id")) != SOURCE_RUN:
            raise RuntimeError("GitHub returned a different source run")
        status = {"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                  "status": run.get("status"), "conclusion": run.get("conclusion")}
        report["polls"].append(status)
        report["source_head_sha"] = run.get("head_sha")
        write_json(output / "source-run-poll.json", report)
        print(json.dumps(status), flush=True)
        if run.get("status") == "completed":
            report["completed"] = True
            write_json(output / "source-run-poll.json", report)
            return
        remaining = deadline - time.time()
        if remaining > 0:
            time.sleep(min(20, remaining))
    report["completed"] = False
    report["error"] = {"kind": "source_run_wait_timeout"}
    write_json(output / "source-run-poll.json", report)
    raise SystemExit("Source run did not complete within the bounded verification wait")


def expected_parts():
    source = HERE.parent / "bora_service_card" / "spec.py"
    tree = ast.parse(source.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PARTS" for t in node.targets):
            parts = ast.literal_eval(node.value)
            if len(parts) == 4 and all(isinstance(p, str) and p for p in parts):
                return parts
    raise RuntimeError("Expected four public paragraphs in the checked-out source")


def prepare_model(cache, output):
    from huggingface_hub import HfApi, snapshot_download
    os.environ.update(HF_HUB_DISABLE_IMPLICIT_TOKEN="1", HF_HUB_DISABLE_TELEMETRY="1")
    # One official model-info lookup records the resolved commit for the pinned model.
    resolved = HfApi(token=False).model_info(MODEL_REPOSITORY, revision=MODEL_REVISION).sha
    if resolved != MODEL_REVISION:
        raise RuntimeError("ASR model revision did not resolve to the expected commit")
    snapshot_download(MODEL_REPOSITORY, revision=resolved, local_dir=cache,
                      allow_patterns=list(MODEL_FILES), token=False, max_workers=4)
    provenance = {"model_repository": MODEL_REPOSITORY,
                  "resolved_model_revision": resolved, "model_files": {}}
    for name in MODEL_FILES:
        path = cache / name
        with path.open("rb") as stream:
            file_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        provenance["model_files"][name] = {"bytes": path.stat().st_size, "sha256": file_hash}
    write_json(output / "asr-model.json", provenance)
    print("Pinned public ASR model prepared; weights remain outside the report artifact.")
    return provenance


def transcribe(artifacts, cache, output):
    report = {"schema": "bora-public-service-card-asr/v1", "source_run": SOURCE_RUN,
              "model_repository": MODEL_REPOSITORY, "requested_model_revision": MODEL_REVISION,
              "device": "cpu", "compute_type": "int8", "cpu_threads": 4,
              "beam_size": 5, "temperature": 0.0, "language": "ko",
              "initial_prompt": None, "condition_on_previous_text": False,
              "vad_filter": False, "word_timestamps": True,
              "quality_qualified": False, "parts": [], "status": "starting"}
    report_path = output / "asr-report.json"
    write_json(report_path, report)
    try:
        import numpy as np
        import soundfile as sf
        from scipy.signal import resample_poly
        from faster_whisper import WhisperModel
        report["packages"] = {name: importlib.metadata.version(name) for name in
                              ("faster-whisper", "ctranslate2", "huggingface-hub", "av", "numpy", "soundfile", "scipy")}
        provenance_path = output / "asr-model.json"
        provenance = (json.loads(provenance_path.read_text()) if provenance_path.exists()
                      else prepare_model(cache, output))
        if provenance["resolved_model_revision"] != MODEL_REVISION:
            raise RuntimeError("Prepared ASR model revision differs from the fixed revision")
        report.update(provenance)
        write_json(report_path, report)
        parts = expected_parts()
        manifests = {}
        for path in artifacts.rglob("run_manifest.json"):
            content = json.loads(path.read_text())
            number = content.get("sample_part")
            if type(number) is not int or number not in range(4):
                raise RuntimeError("Downloaded artifact has an unexpected paragraph index")
            if number in manifests:
                raise RuntimeError("Duplicate paragraph artifacts in the fixed run")
            manifests[number] = (path, content)
        model = WhisperModel(str(cache), device="cpu", compute_type="int8", cpu_threads=4,
                             num_workers=1, local_files_only=True)
        for number, expected in enumerate(parts):
            item = {"part": number, "expected_text_sha256": hashlib.sha256(expected.encode()).hexdigest(),
                    "expected_characters": len(expected), "status": "starting"}
            report["parts"].append(item)
            write_json(report_path, report)
            try:
                source, manifest = manifests[number]
                if (manifest.get("public_text") != expected
                        or manifest.get("public_text_sha256") != item["expected_text_sha256"]
                        or manifest.get("only_profile") != "python_fp32_t4"):
                    raise RuntimeError("Artifact text/hash/profile does not match the public source")
                wave = source.parent / "python_fp32_t4" / "audio.wav"
                info = sf.info(wave)
                if info.frames <= 0 or info.samplerate != 24000 or info.channels != 1:
                    raise RuntimeError("Unexpected source audio format or empty audio")
                with wave.open("rb") as stream:
                    item["audio_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
                item["audio_seconds"] = info.frames / info.samplerate
                generation = manifest["results"][0]
                if generation.get("audio_sha256") != item["audio_sha256"]:
                    raise RuntimeError("Source audio hash differs from the synthesis manifest")
                item["generation_status"] = generation.get("status")
                item["generation_natural_eos"] = generation.get("natural_eos")
                audio, _ = sf.read(wave, dtype="float32")
                audio_16k = resample_poly(audio, 2, 3).astype(np.float32)
                started = time.perf_counter()
                segments, metadata = model.transcribe(
                    audio_16k, language="ko", task="transcribe", beam_size=5, best_of=5,
                    temperature=0.0, condition_on_previous_text=False,
                    vad_filter=False, word_timestamps=True,
                )
                rows = []
                for segment in segments:
                    rows.append({"start": segment.start, "end": segment.end, "text": segment.text,
                                 "avg_logprob": segment.avg_logprob, "no_speech_prob": segment.no_speech_prob,
                                 "words": [{"start": w.start, "end": w.end, "word": w.word,
                                            "probability": w.probability} for w in (segment.words or [])]})
                transcript = "".join(row["text"] for row in rows).strip()
                item.update(status="transcribed_needs_review", transcript=transcript, segments=rows,
                            asr_seconds=time.perf_counter() - started,
                            detected_language=metadata.language, comparison=comparison(expected, transcript))
                print(f"PART {number + 1}: {transcript}", flush=True)
                print(json.dumps({"part": number, "differences": item["comparison"]["differences"]}, ensure_ascii=False), flush=True)
            except Exception as error:
                item.update(status="failed", error=type(error).__name__ + ": " + str(error))
            write_json(report_path, report)
        report["status"] = ("transcribed_all_needs_review" if all(p["status"] == "transcribed_needs_review"
                            for p in report["parts"]) else "partial_or_failed")
        if report["status"] != "transcribed_all_needs_review":
            raise SystemExit("At least one public paragraph could not be verified")
    except Exception as error:
        report.update(status="failed", error=type(error).__name__ + ": " + str(error))
        raise
    finally:
        write_json(report_path, report)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "wait", "transcribe"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--model-cache", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.mode == "wait":
        wait_for_source(args.output)
    elif args.mode == "prepare":
        if args.model_cache is None:
            parser.error("prepare requires --model-cache")
        prepare_model(args.model_cache, args.output)
    else:
        if args.artifacts is None or args.model_cache is None:
            parser.error("transcribe requires --artifacts and --model-cache")
        transcribe(args.artifacts, args.model_cache, args.output)


if __name__ == "__main__":
    main()
