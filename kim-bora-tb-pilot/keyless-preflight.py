"""Public, keyless end-to-end runtime smoke; never opens a lecture manuscript.

One generic sentence is synthesized once. Independent ASR, both production DSP
chains, MP3 encode/decode and exact PCM sample counts must all work before the
protected matrix is scheduled. No audio artifact is uploaded by this job.
"""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'research'/'reading-quality'))
PUBLIC_SMOKE_TEXT='안녕하세요. 오늘도 차근차근 함께 공부해요.'
EXPECTED_PACKAGES={'torch':'2.8.0+cpu','torchaudio':'2.8.0+cpu','qwen-tts':'0.1.1',
    'transformers':'4.57.3','faster-whisper':'1.2.1','soundfile':'0.14.0',
    'cryptography':'50.0.0','numpy':'2.5.4','scipy':'1.18.1','onnxruntime':'1.31.1'}


def require_keyless(env=os.environ):
    if any(env.get(name) for name in ('ADMIN_LAW_PREMIUM_SOURCE_KEY_B64','ADMIN_LAW_PREMIUM_MEDIA_KEY_B64')):
        raise RuntimeError('public smoke must not receive protected keys')


def package_versions():
    versions={name:importlib.metadata.version(name) for name in EXPECTED_PACKAGES}
    if versions!=EXPECTED_PACKAGES:
        raise RuntimeError('public smoke dependency versions differ from rendering pins')
    return versions


def main():
    require_keyless()
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    from pilot_render import (assert_offline,Renderer,FILTERS,signal_stats,
                              trimmed_faded,unit_key)
    from paragraph_plan import build_plan
    from download_models import verify_assets,QWEN_REQUIRED,ASR_REQUIRED,MODEL_REVISION,ASR_REVISION
    from run import validate_config,validate_request,enc,digest
    assert_offline()
    if os.geteuid()==0:
        raise RuntimeError('smoke must use the same unprivileged runner UID as rendering')
    versions=package_versions()
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('ffmpeg is missing before any inference')
    subprocess.run([ffmpeg,'-version'],capture_output=True,check=True)
    sox=shutil.which('sox')
    if sox:
        subprocess.run([sox,'--version'],capture_output=True,check=True)
    qwen=Path(os.environ['BORA_MODEL']);asr=Path(os.environ['BORA_ASR'])
    verify_assets(qwen,QWEN_REQUIRED);verify_assets(asr,ASR_REQUIRED)
    model_config=json.loads((qwen/'config.json').read_text())
    if (model_config.get('model_type')!='qwen3_tts' or model_config.get('tts_model_type')!='custom_voice'
            or model_config.get('tts_model_size')!='1b7'):
        raise ValueError('wrong model architecture')
    cfg=validate_config(json.loads((HERE/'config.json').read_text()))
    validate_request(json.loads((HERE/'request.json').read_text()),cfg)
    parent=Path(os.environ['RUNNER_TEMP'])
    if not parent.is_dir() or not os.access(parent,os.W_OK):
        raise RuntimeError('runner temporary directory is not writable')
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='bora-public-smoke-',dir=parent) as tmp:
        directory=Path(tmp)
        if directory.stat().st_uid!=os.geteuid():
            raise RuntimeError('smoke output UID mismatch')
        plan=build_plan(PUBLIC_SMOKE_TEXT,'TB-001',180)
        settings=argparse.Namespace(output=directory,model=qwen,asr=asr,threads=2,
            sample_rate=24000,max_new_tokens=350,max_cer=.35,paragraph_gap=.35,unit_gap=.18)
        renderer=Renderer(settings,plan)
        unit=plan['units'][0]
        candidate=renderer.candidate(unit,0,5192,unit_key(plan,unit,renderer.settings))
        if candidate['signal']['blocked'] or candidate['asr']['blocked'] or not candidate['asr'].get('words'):
            raise RuntimeError('public synthesis or independent ASR smoke failed')
        source=directory/'units/0000'/candidate['file']
        results=[]
        for mode in ('explain','reading'):
            processed=directory/(mode+'.processed.wav')
            subprocess.run([ffmpeg,'-v','error','-y','-i',str(source),'-af',FILTERS[mode],
                '-ar','24000','-ac','1','-c:a','pcm_f32le',str(processed)],check=True)
            wave,sr=renderer.sf.read(processed,dtype='float32');wave,_=trimmed_faded(wave,sr)
            signal=signal_stats(wave,sr)
            if signal['blocked'] or sr!=24000:
                raise RuntimeError('production DSP smoke failed')
            final=directory/(mode+'.wav');mp3=directory/(mode+'.mp3');decoded=directory/(mode+'.decoded.wav')
            renderer.sf.write(final,wave,sr,subtype='FLOAT')
            subprocess.run([ffmpeg,'-v','error','-y','-i',str(final),'-c:a','libmp3lame','-q:a','2',str(mp3)],check=True)
            subprocess.run([ffmpeg,'-v','error','-y','-i',str(mp3),'-ac','1','-ar','24000','-c:a','pcm_f32le',str(decoded)],check=True)
            checked,checked_sr=renderer.sf.read(decoded,dtype='float32');after=signal_stats(checked,checked_sr)
            if after['blocked'] or checked_sr!=sr or len(checked)!=len(wave):
                raise RuntimeError('MP3 decoded samples or signal differ from measured PCM')
            results.append(dict(mode=mode,sample_rate=sr,samples=len(wave),decoded_samples=len(checked),seconds=len(wave)/sr))
        report=dict(schema='kim-bora-keyless-preflight/v1',status='public_runtime_smoke_passed',
            public_smoke_text=PUBLIC_SMOKE_TEXT,source_key_used=False,private_manuscript_read=False,
            uid=os.geteuid(),ffmpeg=ffmpeg,sox=sox,libsndfile=renderer.sf.__libsndfile_version__,
            packages=versions,model_revision=MODEL_REVISION,asr_revision=ASR_REVISION,
            generic_audio_seconds=candidate['signal']['seconds'],asr_cer=candidate['asr']['cer'],
            asr_word_count=len(candidate['asr']['words']),dsp_checks=results,
            config_sha256=digest(enc(cfg)),matrix=dict(group=list(range(len(cfg['groups'])))))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(status=report['status'],generic_audio_seconds=report['generic_audio_seconds'],
        asr_cer=report['asr_cer'],dsp_modes=len(results),groups=len(cfg['groups']))))


if __name__=='__main__':
    main()
