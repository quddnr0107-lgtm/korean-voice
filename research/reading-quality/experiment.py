"""Offline, bounded multi-seed Qwen3 CustomVoice/Whisper experiment.
Execute in a separate network namespace; local models only. No paid API.
Example:
 BORA_HOST_NETNS=$(readlink /proc/self/ns/net) unshare -Urn python experiment.py \
  --text private.txt --tts-model /models/custom17 --asr-model /models/whisper-small \
  --out /private/experiment --instruction 'Conversational Korean.'
"""
import argparse, gc, json, os, shutil
from pathlib import Path

def main():
    parser=argparse.ArgumentParser()
    for name in ('text','tts-model','asr-model','out'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--instruction',required=True)
    parser.add_argument('--seeds',default='5192,7319')
    parser.add_argument('--max-new-tokens',type=int,default=650)
    args=parser.parse_args()
    if os.readlink('/proc/self/ns/net')==os.environ.get('BORA_HOST_NETNS',''):
        raise RuntimeError('isolated network namespace required')
    if not os.environ.get('BORA_HOST_NETNS'):
        raise RuntimeError('host namespace marker required')
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
                      HF_HUB_DISABLE_TELEMETRY='1',DO_NOT_TRACK='1',ORT_DISABLE_TELEMETRY='1')
    import onnxruntime as ort
    ort.disable_telemetry_events()
    import torch, soundfile as sf, numpy as np
    from scipy.signal import resample_poly
    from qwen_tts import Qwen3TTSModel
    from faster_whisper import WhisperModel
    from candidate_eval import evaluate
    seeds=[int(s) for s in args.seeds.split(',')]
    if not 1<=len(seeds)<=4:raise ValueError('bounded experiment: 1..4 seeds')
    text=args.text.read_text().strip()
    if not text or len(text)>500:raise ValueError('use a complete semantic excerpt, 1..500 characters')
    args.out.mkdir(parents=True,exist_ok=True)
    torch.set_num_threads(4)
    model=Qwen3TTSModel.from_pretrained(str(args.tts_model),device_map='cpu',
              dtype=torch.bfloat16,attn_implementation='sdpa',local_files_only=True)
    rows=[]
    for seed in seeds:
        torch.manual_seed(seed)
        wav=args.out/f'candidate-{seed}.wav'
        if wav.exists():raise FileExistsError('use a fresh experiment directory')
        audio,sr=model.generate_custom_voice(text=text,language='Korean',speaker='Sohee',
              instruct=args.instruction,non_streaming_mode=True,max_new_tokens=args.max_new_tokens,
              do_sample=True,temperature=.8,top_p=.9)
        sf.write(wav,audio[0],sr)
        rows.append(dict(name=str(seed),seed=seed,wav=str(wav.resolve()),expected=text))
        (args.out/'checkpoint.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
    del model;gc.collect()
    recognizer=WhisperModel(str(args.asr_model),device='cpu',compute_type='int8',
                          cpu_threads=2,num_workers=1,local_files_only=True)
    for row in rows:
        audio,sr=sf.read(row['wav'])
        segments,_=recognizer.transcribe(resample_poly(audio,16000,sr).astype(np.float32),
              language='ko',beam_size=5,temperature=0,condition_on_previous_text=False,vad_filter=False)
        row['recognized']=' '.join(s.text.strip() for s in segments)
    result=evaluate(rows)
    result['generation']=dict(model=str(args.tts_model),instruction=args.instruction,
              seeds=seeds,temperature=.8,top_p=.9,non_streaming_mode=True,
              max_new_tokens=args.max_new_tokens)
    (args.out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    selected=next(r for r in rows if r['name']==result['selected'])
    shutil.copyfile(selected['wav'],args.out/'selected.wav')
    print(json.dumps(dict(selected=result['selected'],release_status=result['release_status'])))

if __name__=='__main__':main()
