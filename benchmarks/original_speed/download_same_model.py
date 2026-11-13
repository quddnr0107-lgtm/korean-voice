"""Fetch one public, revision-pinned existing Kim Bora model; no inference."""
import argparse
import hashlib
import json
import os
from pathlib import Path

REPO = 'Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice'
REVISION = '0c0e3051f131929182e2c023b9537f8b1c68adfe'
HASHES = {
 'model.safetensors':'38b1d5971bdbd982b561cccec982669a53b0537c3cf5e9bd4778ed07bb2f5137',
 'speech_tokenizer/model.safetensors':'836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258',
 'config.json':'17a07f527a1c25ea30b4e023a184482a23d3e279d697b1dc81b1bde498d29cf9',
 'generation_config.json':'f1b90b4513f3b34c62851049e2492d7b4c5940daf1276f89c82b8ef04127f3aa',
 'tokenizer_config.json':'dc3c31c3bdaedd5016382bb3cbe07323026775ad51f5a4fb564505992ae4a670',
 'preprocessor_config.json':'efdde1022ea9d76928bf7a9cd53139138f5ba2e466e837f08f6105ab1af1c119',
 'vocab.json':'ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910',
 'merges.txt':'599bab54075088774b1733fde865d5bd747cbcc7a547c5bc12610e874e26f5e3',
 'speech_tokenizer/config.json':'ee65bb901c876664ab8707c487157aa1a6ee57c65969b28fb5ec9dc211e68167',
 'speech_tokenizer/configuration.json':'6bc26d64eb5024b4d1dab5a52371958b429256d6c9d59787f1f5294a54e0cebd',
 'speech_tokenizer/preprocessor_config.json':'fcb3805e597e786d4067706e602f6688524640f8d3396790e2e09b5942fcbdfb',
}

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args()
    if any(os.environ.get(n) for n in ('ADMIN_LAW_PREMIUM_SOURCE_KEY_B64','ADMIN_LAW_PREMIUM_MEDIA_KEY_B64')):
        raise RuntimeError('public_download_rejects_protected_keys')
    from huggingface_hub import snapshot_download
    root=Path(a.output)
    snapshot_download(REPO,revision=REVISION,local_dir=root,allow_patterns=list(HASHES),max_workers=4)
    observed={}
    for name,expected in HASHES.items():
        with (root/name).open('rb') as f: observed[name]=hashlib.file_digest(f,'sha256').hexdigest()
        if observed[name]!=expected:raise RuntimeError('fixed_model_digest_mismatch:'+name)
    (root/'original-bora-verified.json').write_text(json.dumps(dict(repository=REPO,revision=REVISION,sha256=observed),indent=2))
    print(json.dumps(dict(status='same_original_model_verified',files=len(observed),revision=REVISION)))

if __name__=='__main__':main()
