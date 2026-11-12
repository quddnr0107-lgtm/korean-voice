"""One original Python-engine inference, in a disposable child process."""
import argparse
import json
import os
from pathlib import Path
import resource
import time

from spec import INSTRUCTION, MAX_TOKENS, PUBLIC_TEXT, SEED


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dtype", choices=("bf16", "fp32"), required=True)
    parser.add_argument("--threads", type=int, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    metrics = {"stage": "imports", "pid": os.getpid()}

    def save():
        metrics["max_rss_kib_observed"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        target = args.output / "worker.json"
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(metrics, indent=2) + "\n")
        temp.replace(target)

    save()
    import torch
    import soundfile as sf
    from qwen_tts import Qwen3TTSModel
    torch.set_num_threads(args.threads)
    metrics.update(stage="loading", torch_threads=torch.get_num_threads(),
                   torch_interop_threads=torch.get_num_interop_threads(),
                   torch_cpu_capability=torch.backends.cpu.get_cpu_capability())
    save()
    start = time.perf_counter()
    model = Qwen3TTSModel.from_pretrained(
        str(args.model), device_map="cpu",
        dtype=torch.bfloat16 if args.dtype == "bf16" else torch.float32,
        attn_implementation="sdpa", low_cpu_mem_usage=True, local_files_only=True,
    )
    metrics.update(cold_model_load_seconds=time.perf_counter() - start, stage="loaded")
    save()
    original = model.model.talker.generate
    eos_id = model.model.config.talker_config.codec_eos_token_id

    def observe_generate(*positional, **keywords):
        result = original(*positional, **keywords)
        sequence = result.sequences[0]
        metrics.update(generated_sequence_tokens=int(sequence.numel()),
                       eos_token_id=int(eos_id), eos_seen=bool((sequence == eos_id).any()),
                       last_token=int(sequence[-1]) if sequence.numel() else None)
        return result

    model.model.talker.generate = observe_generate
    torch.manual_seed(SEED)
    start = time.perf_counter()
    with torch.inference_mode():
        waves, rate = model.generate_custom_voice(
            text=PUBLIC_TEXT, language="Korean", speaker="Sohee", instruct=INSTRUCTION,
            non_streaming_mode=True, max_new_tokens=MAX_TOKENS, do_sample=True,
            temperature=0.8, top_p=0.9, top_k=50, repetition_penalty=1.05,
            subtalker_dosample=True, subtalker_temperature=0.9,
            subtalker_top_p=1.0, subtalker_top_k=50,
        )
    metrics.update(tts_seconds=time.perf_counter() - start, stage="generated",
                   samples=len(waves[0]), sample_rate=int(rate))
    save()
    sf.write(args.output / "audio.wav", waves[0], rate, subtype="FLOAT")
    capped = metrics.get("generated_sequence_tokens", MAX_TOKENS) >= MAX_TOKENS
    metrics.update(stage="finished", cap_reached=capped,
                   natural_eos=(metrics.get("eos_seen") is True
                                and metrics.get("last_token") == eos_id and not capped))
    save()
    if not metrics["natural_eos"]:
        raise SystemExit("FAIL: token cap reached or natural EOS was not verified")


if __name__ == "__main__":
    main()
