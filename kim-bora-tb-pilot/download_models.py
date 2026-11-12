"""Keyless revision-pinned asset download; never imports TTS inference."""
import json
import os
from pathlib import Path

MODEL_REPOSITORY = 'Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice'
MODEL_REVISION = '0c0e3051f131929182e2c023b9537f8b1c68adfe'
ASR_REPOSITORY = 'Systran/faster-whisper-small'
ASR_REVISION = '536b0662742c02347bc0e980a01041f333bce120'
QWEN_REQUIRED = {
    'model.safetensors':3833402552, 'speech_tokenizer/model.safetensors':682293092,
    'config.json':None, 'generation_config.json':None, 'tokenizer_config.json':None,
    'preprocessor_config.json':None, 'vocab.json':None, 'merges.txt':None,
    'speech_tokenizer/config.json':None, 'speech_tokenizer/configuration.json':None,
    'speech_tokenizer/preprocessor_config.json':None,
}
ASR_REQUIRED = {'model.bin':483546902,'config.json':None,'tokenizer.json':None,'vocabulary.txt':None}


def require_keyless():
    if any(os.environ.get(name) for name in ('ADMIN_LAW_PREMIUM_SOURCE_KEY_B64','ADMIN_LAW_PREMIUM_MEDIA_KEY_B64')):
        raise RuntimeError('model download must not receive source or media keys')


def verify_assets(root, required):
    root=Path(root)
    for name, expected_size in required.items():
        path=root/name
        if not path.is_file() or path.is_symlink():
            raise ValueError('missing or symlinked model asset: '+name)
        size=path.stat().st_size
        if size <= 0 or (expected_size is not None and size != expected_size):
            raise ValueError('incomplete pinned model asset: '+name)
        if name.endswith('.json'):
            json.loads(path.read_text())


def main():
    require_keyless()
    os.environ.update(HF_HUB_DISABLE_TELEMETRY='1', DO_NOT_TRACK='1')
    from huggingface_hub import snapshot_download
    root=Path(os.environ['RUNNER_TEMP'])
    models=[
        ('BORA_MODEL',MODEL_REPOSITORY,MODEL_REVISION,'bora-qwen',QWEN_REQUIRED),
        ('BORA_ASR',ASR_REPOSITORY,ASR_REVISION,'bora-asr',ASR_REQUIRED),
    ]
    with open(os.environ['GITHUB_ENV'],'a') as env:
        for name,repository,revision,folder,required in models:
            target=root/folder
            snapshot_download(repository,revision=revision,local_dir=target,
                              allow_patterns=list(required),max_workers=4)
            verify_assets(target,required)
            env.write(name+'='+str(target)+'\n')
    print(json.dumps(dict(status='pinned_assets_downloaded_and_checked',models=len(models))))


if __name__=='__main__':
    main()
