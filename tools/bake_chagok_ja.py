#!/usr/bin/env python3
"""Japanese fixed-script bake, isolated from English audio.

Generate identical audio for all 365 days, without altering production /tts,
R2 cache, or the existing Korean narration pipeline.
"""
import argparse, json, os, re, subprocess, sys
from pathlib import Path
import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "chagok" / "365-fixed-transcript.txt"

def parse():
    items = []
    scene = -1
    for raw in SRC.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if raw.startswith("[") and s.endswith("]"):
            scene += 1
        elif m := re.match(r"^(\d+)\.\s+(.+)$", s):
            items.append({"number": int(m.group(1)), "scene": scene, "en": m.group(2), "ko": ""})
        elif raw.startswith("   ") and s and items and not items[-1]["ko"]:
            items[-1]["ko"] = s
    assert len(items) == 389, f"Incorrect item count: {len(items)}"
    assert max(it["scene"] for it in items) == 16, "Expected 17 ordered scenes"
    assert [it["number"] for it in items] == list(range(1, 390))
    assert all(it["en"] and it["ko"] for it in items), "Missing translation"
    return items

def trim(w, sr):
    w = np.asarray(w, dtype=np.float32).reshape(-1)
    if not len(w): raise ValueError("Empty speech waveform")
    # Preserve initial consonants: only trim long digital silence.
    threshold = max(0.001, float(np.max(np.abs(w))) * 0.012)
    nonzero = np.flatnonzero(np.abs(w) > threshold)
    if len(nonzero):
        pad = int(sr * .035)
        w = w[max(0,int(nonzero[0])-pad):min(len(w),int(nonzero[-1])+pad)]
    f = min(len(w)//2, int(sr*.012))
    if f:
        ramp = np.linspace(0, 1, f, dtype=np.float32)
        w[:f] *= ramp
        w[-f:] *= ramp[::-1]
    peak = max(float(np.max(np.abs(w))), 0.001)
    return w * min(1.0, 0.82/peak)

def japanese_items():
    items = parse()
    ja = []
    for name in ("ja-001-120.json", "ja-121-264.json", "ja-265-389.json"):
        ja.extend(json.loads((ROOT / "chagok" / name).read_text(encoding="utf-8")))
    assert len(ja) == 389 and all(isinstance(x,str) and x.strip() for x in ja)
    for item, text in zip(items, ja):
        item["ja"] = text
    return items

def bake(args):
    data = [x for x in japanese_items() if x["scene"] == args.scene]
    assert data and 0 <= args.scene <= 16
    assets = Path(args.assets).resolve()
    sys.path.insert(0, str(assets/"py"))
    import helper
    tts = helper.load_text_to_speech(str(assets/"onnx"), False)
    style = helper.load_voice_style([str(assets/"voice_styles"/"F2.json")])
    sr = int(tts.sample_rate)
    def speak(text, lang):
        wave, duration = tts._infer([text], [lang], style, args.steps, 1.03)
        w = np.asarray(wave).reshape(-1)
        actual = min(len(w), int(float(np.asarray(duration).reshape(-1)[0])*sr))
        assert actual > 0 and np.isfinite(w).all()
        w = trim(w[:actual], sr)
        assert len(w) >= sr*.15 and np.max(np.abs(w)) > .005
        return w
    def silence(s):
        return np.zeros(round(sr*s), np.float32)
    learn, listen, cues = [], [], []
    l = e = 0
    for item in data:
        jp = speak(item["ja"], "ja")
        ko = speak(item["ko"], "ko")
        a, b, c = silence(.80), silence(.90), silence(1.30)
        d = silence(2.40 if item["ja"].rstrip().endswith(("？","?")) else .86)
        kstart = l + len(jp) + len(a)
        jstart = kstart + len(ko) + len(b)
        lend = jstart + len(jp) + len(c)
        eend = e + len(jp) + len(d)
        cues.append([item["number"], l, kstart, jstart, lend, e, eend])
        learn += [jp,a,ko,b,jp,c]
        listen += [jp,d]
        l,e = lend,eend
        print(f"scene={args.scene+1} line={item['number']} ja={len(jp)/sr:.3f}s ko={len(ko)/sr:.3f}s", flush=True)
    dest = Path(args.out); dest.mkdir(parents=True,exist_ok=True)
    for mode, parts, expected in (("learn",learn,l),("listen",listen,e)):
        w = np.concatenate(parts)
        assert len(w)==expected
        p=dest/f"{args.scene:02d}-{mode}.flac"
        sf.write(p,w,sr,format="FLAC",subtype="PCM_24")
        info=sf.info(p)
        assert info.frames==expected and info.samplerate==sr and info.channels==1
    (dest/f"{args.scene:02d}.json").write_text(json.dumps({"scene":args.scene,"sr":sr,"cues":cues}), encoding="utf-8")

def combine(args):
    src=Path(args.input).resolve(); dest=Path(args.out).resolve(); dest.mkdir(parents=True,exist_ok=True)
    l=e=0; sr=None; cues=[]
    assert len(list(src.glob("*.flac")))==34 and len(list(src.glob("*.json")))==17
    for scene in range(17):
        meta=json.loads((src/f"{scene:02d}.json").read_text())
        assert meta["scene"]==scene
        if sr is None: sr=meta["sr"]
        assert sr==meta["sr"]
        c=meta["cues"]
        expected=[x["number"] for x in japanese_items() if x["scene"]==scene]
        assert [x[0] for x in c]==expected
        assert c[0][1]==0 and c[0][5]==0
        for i, row in enumerate(c):
            n,a,b,d,z,x,y=row
            assert a<b<d<z and x<y
            if i: assert a==c[i-1][4] and x==c[i-1][6]
            cues.append([n,*[round((q+l)/sr,6) for q in row[1:5]],*[round((q+e)/sr,6) for q in row[5:7]]])
        for mode,last in (("learn",c[-1][4]),("listen",c[-1][6])):
            info=sf.info(src/f"{scene:02d}-{mode}.flac")
            assert info.frames==last and info.samplerate==sr and info.channels==1
        l+=c[-1][4];e+=c[-1][6]
    assert [x[0] for x in cues]==list(range(1,390))
    durations={}
    for mode,frames in (("learn",l),("listen",e)):
        lst=dest/f"{mode}.txt"
        lst.write_text("\n".join(f"file '{src / f'{i:02d}-{mode}.flac'}'" for i in range(17))+"\n")
        target=dest/f"chagok-ja-365-{mode}.mp3"
        subprocess.run(["ffmpeg","-nostdin","-v","error","-y","-f","concat","-safe","0","-i",str(lst),"-ar","24000","-ac","1","-codec:a","libmp3lame","-b:a","96k",str(target)],check=True)
        actual=float(subprocess.check_output(["ffprobe","-v","error","-show_entries","format=duration","-of","default=nw=1:nk=1",str(target)],text=True))
        assert abs(actual-frames/sr)<.15 and target.stat().st_size>1024
        subprocess.run(["ffmpeg","-nostdin","-v","error","-xerror","-i",str(target),"-f","null","-"],check=True)
        durations[mode]=actual
        lst.unlink()
    meta={"version":1,"count":389,"unit":"seconds","durations":durations,"format":"[number, learn_start, korean_start, japanese_again_start, learn_end, listen_start, listen_end]","cues":cues}
    (dest/"chagok-ja-365-cues.json").write_text(json.dumps(meta,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
    transcript=["차곡일본어 365일 고정 학습 대본 — 총 389문장",""]
    scenes=re.findall(r"^\[.+\]$", SRC.read_text(encoding="utf-8"),re.M)
    last=-1
    for item in japanese_items():
        if last!=item["scene"]:
            last=item["scene"];transcript.extend(["",scenes[last]])
        transcript.extend([f"{item['number']}. {item['ja']}",f"   {item['ko']}"])
    (dest/"chagok-ja-365-transcript.txt").write_text("\n".join(transcript)+"\n",encoding="utf-8")
    print(json.dumps({"count":len(cues),"durations":durations}))

if __name__ == "__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--scene",type=int)
    p.add_argument("--assets",default="supertonic3")
    p.add_argument("--out",required=True)
    p.add_argument("--steps",type=int,default=16)
    p.add_argument("--input")
    p.add_argument("--verify-only",action="store_true")
    a=p.parse_args()
    if a.verify_only:
        print("Verified",len(japanese_items()),"aligned lines")
    elif a.input: combine(a)
    else: bake(a)
