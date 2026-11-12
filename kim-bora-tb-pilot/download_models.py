"""Keyless revision-pinned asset download; never imports TTS inference."""
import json
import os
from pathlib import Path

if os.environ.get('ADMIN_LAW_PREMIUM_SOURCE_KEY_B64'):
    raise RuntimeError('model download must not receive the source key')
os.environ.update(HF_HUB_DISABLE_TELEMETRY='1', DO_NOT_TRACK='1')
from huggingface_hub import snapshot_download
root = Path(os.environ['RUNNER_TEMP'])
models = [
    ('BORA_MODEL', 'Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice', '0c0e3051f131929182e2c023b9537f8b1c68adfe', 'bora-qwen', ['*.json', '*.safetensors', '*.txt']),
    ('BORA_ASR', 'Systran/faster-whisper-small', '536b0662742c02347bc0e980a01041f333bce120', 'bora-asr', ['model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt']),
]
with open(os.environ['GITHUB_ENV'], 'a') as env:
    for name, repository, revision, folder, patterns in models:
        target = root/folder
        snapshot_download(repository, revision=revision, local_dir=target, allow_patterns=patterns)
        env.write(name+'='+str(target)+'\n')
print(json.dumps(dict(status='pinned_assets_downloaded', models=len(models))))
