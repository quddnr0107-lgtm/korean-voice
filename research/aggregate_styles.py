"""흡수 결과 집계 — 문체(낭독/대화)와 코퍼스를 나눠 궤적표를 만든다.

핵심: 낭독체와 대화체는 억양 체계가 다르다(문두에서 이미 정반대로 나왔다).
강의 음성은 낭독보다 대화에 가까우므로 둘을 섞어 평균 내면 둘 다 아닌 것이 된다.
"""
import glob, json, collections, os
import numpy as np

R = '/tmp/claude-0/-home-user-militaryapplyhelper/3cdcc1ab-2560-5ce9-87a4-c23e74e7dd5a'

# 파일 이름 → (코퍼스, 문체)
def origin(path):
    b = os.path.basename(path)
    if b.startswith('K_'):
        return 'KsponSpeech', '대화'
    if b.startswith('F_'):
        return 'FLEURS', '낭독'
    if b.startswith('C_'):
        return 'CommonVoice', '낭독'
    if b.startswith('A_zeroth') or b.startswith('s'):
        return 'Zeroth', '낭독'
    if b.startswith('A_zerothSTT'):
        return 'Zeroth-STT', '낭독'
    if b.startswith('A_daje'):
        return 'daje-TTS', '낭독'
    if b.startswith('A_kojaCS'):
        return '코드스위칭', '낭독'
    if b.startswith('c') or b.startswith('t'):
        return 'Zeroth', '낭독'
    return '기타', '낭독'


def merge(parts):
    """[(5점배열, n)] → 가중평균 5점, 총 n"""
    tot = sum(n for _, n in parts)
    if tot == 0:
        return None, 0
    v = sum(np.asarray(m, float) * n for m, n in parts) / tot
    return [round(float(x), 3) for x in v], tot


if __name__ == '__main__':
    by_style = collections.defaultdict(lambda: collections.defaultdict(list))   # 문체 → 위치 → parts
    by_style_end = collections.defaultdict(lambda: collections.defaultdict(list))
    by_corpus = collections.defaultdict(lambda: {'nw': 0, 'utt': 0})

    files = glob.glob(R + '/K_*.log') + glob.glob(R + '/F_*.log') + glob.glob(R + '/C_*.log') + \
        glob.glob(R + '/A_*.log') + glob.glob(R + '/c?.log') + glob.glob(R + '/t?.log') + glob.glob(R + '/s?_?.log')
    for f in files:
        try:
            txt = open(f, encoding='utf-8', errors='ignore').read()
        except Exception:
            continue
        for line in txt.splitlines():
            if not line.startswith('PART '):
                continue
            try:
                d = json.loads(line[5:])
            except Exception:
                continue
            corpus, style = origin(f)
            by_corpus[corpus]['nw'] += d.get('nw', 0)
            by_corpus[corpus]['utt'] += d.get('utt', 0)
            for k, (m, n) in d.get('shape', {}).items():
                by_style[style][k].append((m, n))
            for k, (m, n) in d.get('grid', {}).items():
                by_style_end[style][k].append((m, n))

    print('── 흡수 현황 ──')
    tw = 0
    for c, v in sorted(by_corpus.items(), key=lambda kv: -kv[1]['nw']):
        tw += v['nw']
        print(f'  {c:14s} 어절 {v["nw"]:7d}' + (f' · 발화 {v["utt"]}' if v['utt'] else ''))
    print(f'  {"합계":14s} 어절 {tw:7d}')

    out = {}
    for style in ('낭독', '대화'):
        if style not in by_style:
            continue
        print(f'\n── {style}체 위치별 궤적 (반음) ──')
        pos = {}
        for k in sorted(by_style[style]):
            m, n = merge(by_style[style][k])
            pos[k] = m
            print(f'  {k}  {m}  n={n}')
        ends = {}
        for k in sorted(by_style_end[style]):
            m, n = merge(by_style_end[style][k])
            if n >= 30:
                ends[k] = {'궤적': m, 'n': n}
        out[style] = {'position': pos, 'ending': ends}
        if ends:
            print(f'  어미별 {len(ends)}종 (표본 30+)')

    if '낭독' in out and '대화' in out:
        print('\n── 낭독 vs 대화 차이 ──')
        for k in ('A문두', 'B문중', 'C문말'):
            a = out['낭독']['position'].get(k); b = out['대화']['position'].get(k)
            if a and b:
                d = [round(y - x, 2) for x, y in zip(a, b)]
                print(f'  {k}  대화−낭독 {d}')
    json.dump(out, open(R + '/style_contours.json', 'w'), ensure_ascii=False, indent=1)
    print('\nstyle_contours.json 저장')
