"""One whole-call INT8 retry; reuse the established minute harness unchanged."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "bora_minute"))
import run_benchmark as original

PROFILE = "native_int8_t4"
SOURCE_RUN = "43173017549"
EXTRA_EOS_ARGS = ["--eos-suppress", "600", "--eos-ramp", "0", "--eos-cap", "0"]
BASE_COMMAND = original.command


def retry_command(profile, model, native, output):
    if profile["id"] != PROFILE:
        raise ValueError("This retry is restricted to native_int8_t4")
    argv = BASE_COMMAND(profile, model, native, output)
    index = argv.index("--eos-strategy") + 1
    if argv[index] != "off":
        raise RuntimeError("Original EOS strategy changed; refusing an ambiguous override")
    argv[index] = "v1"
    argv.extend(EXTRA_EOS_ARGS)
    return argv


def annotate_manifest(output):
    path = output.resolve() / "run_manifest.json"
    manifest = json.loads(path.read_text()) if path.exists() else {
        "schema": "bora-public-minute-int8-retry/v1",
        "status": "original_harness_did_not_finish_manifest",
        "quality_qualified": False,
    }
    manifest["retry"] = {
        "source_original_workflow_run": SOURCE_RUN,
        "source_original_profile": PROFILE,
        "reason": "Original INT8 call ended naturally at 32.56 seconds but omitted the latter half of the shared paragraph.",
        "whole_call": True,
        "text_splicing": False,
        "input_instruction_seed_and_sampling_preserved": True,
        "overrides": {
            "eos_strategy": {"original": "off", "retry": "v1"},
            "eos_suppress_frames": 600,
            "eos_ramp": 0,
            "eos_cap": 0,
        },
        "effect": "Suppress EOS during frames 0 through 599; after that use the original EOS logit without additive boosting. This does not force termination at 48 seconds.",
        "completeness_requires_independent_review": True,
        "wrapper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    original.write_json(path, manifest)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    prior_argv = sys.argv
    original.command = retry_command
    sys.argv = [str(HERE.parent / "bora_minute" / "run_benchmark.py"),
                "--only-profile", PROFILE, "--model", str(args.model),
                "--native", str(args.native), "--output", str(args.output)]
    if args.dry_run:
        sys.argv.append("--dry-run")
    try:
        original.main()
    finally:
        sys.argv = prior_argv
        original.command = BASE_COMMAND
        if not args.dry_run:
            annotate_manifest(args.output)


if __name__ == "__main__":
    main()
