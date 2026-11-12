"""Download only the public, revision-pinned Qwen assets without credentials."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time

from spec import MODEL_FILES, MODEL_REPOSITORY, MODEL_REVISION


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    os.environ.update(HF_HUB_DISABLE_IMPLICIT_TOKEN="1", HF_HUB_DISABLE_TELEMETRY="1")
    from huggingface_hub import snapshot_download
    started = time.perf_counter()
    snapshot_download(MODEL_REPOSITORY, revision=MODEL_REVISION,
                      local_dir=args.model, allow_patterns=list(MODEL_FILES),
                      max_workers=4, token=False)
    files = {}
    for name, expected in MODEL_FILES.items():
        path = args.model / name
        if not path.is_file() or path.is_symlink():
            raise RuntimeError("Missing or symlinked public asset: " + name)
        size = path.stat().st_size
        if size <= 0 or (expected is not None and size != expected):
            raise RuntimeError("Incomplete pinned asset: " + name)
        if name.endswith(".json"):
            json.loads(path.read_text())
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        files[name] = {"bytes": size, "sha256": digest}
    config = json.loads((args.model / "config.json").read_text())
    if (config.get("tts_model_type") != "custom_voice"
            or config.get("tts_model_size") != "1b7"):
        raise RuntimeError("Unexpected model architecture")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({
        "repository": MODEL_REPOSITORY, "revision": MODEL_REVISION,
        "download_and_verify_seconds": time.perf_counter() - started,
        "authenticated": False, "files": files,
    }, indent=2) + "\n")
    print("Public model downloaded and verified at the pinned revision.")


if __name__ == "__main__":
    main()
