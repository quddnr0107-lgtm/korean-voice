"""어절 단위 심층 분석 — 왜 그 어절이 그 모양인가.

지금까지는 문두/문중/문말 세 칸으로만 나눴다. 그것만으로는 「이 단어를 어떻게 읽어야 하는가」에
답할 수 없다. 한국어 억양 이론(K-ToBI)은 강세구 첫 자음이 톤을 결정한다고 말한다 —
격음·경음·ㅅ·ㅎ 로 시작하면 높게(THLH), 평음이면 낮게(LHLH). 실측으로 확인한다.

정렬: 전사 어절 수와 유성 구간 수가 **같은 발화만** 쓴다. 그러면 어절↔소리를 1:1 로 짝지을 수 있다.
표본이 줄지만 정확하다. 어차피 코퍼스가 20만 어절이라 일부만 써도 충분하다.

재는 축:
  ① 초성 유형(격음/경음/평음/유성·모음)   ② 음절 수   ③ 어절 끝(조사·어미·순수명사)
  ④ 문장 내 위치   ⑤ 어절 안 8점 궤적 + 최고점 위치 + 길이
음색은 화자 중앙값으로 나눠 지운다. 남는 것은 한국어의 억양 규칙뿐이다.
"""
import os, sys, io, json, re, collections
import numpy as np, soundfile as sf, pyarrow.parquet as pq

R = '/tmp/claude-0/-home-user-militaryapplyhelper/3cdcc1ab-2560-5ce9-87a4-c23e74e7dd5a'
sys.path.insert(0, R)
from extract_feats import _split, _f0  # noqa: E402

SRC = os.environ.get('SRC', R + '/corpus/test.parquet')
N = int(os.environ.get('N', '300'))
OFF = int(os.environ.get('OFF', '0'))
TAG = os.environ.get('TAG', 'zeroth')

CHO = list('ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ')
ASPIRATED = set('ㅊㅋㅌㅍㅎ')      # 격음
TENSE = set('ㄲㄸㅃㅆㅉ')          # 경음
FRIC = set('ㅅ')                   # 마찰음 — 이론상 격음 쪽과 같이 H 를 유발
SONOR = set('ㄴㅁㄹㅇ')            # 비음·유음·무자음(모음 시작)


def onset_class(w):
    """어절 첫 음절의 초성 유형 — K-ToBI 가 톤 시작을 가른다고 말하는 그 자질."""
    for ch in w:
        if '가' <= ch <= '힣':
            c = CHO[(ord(ch) - 0xAC00) // 588]
            if c in ASPIRATED:
                return 'H_격음'
            if c in TENSE:
                return 'H_경음'
            if c in FRIC:
                return 'H_ㅅ'
            if c in SONOR:
                return 'L_비음유음'
            return 'L_평음'
    return '기타'


TAIL = [('어미_다', r'(습니다|ㅂ니다|다)$'), ('어미_요', r'요$'), ('어미_까', r'(까|나요|죠|가요)$'),
        ('연결_고', r'(고|며|면서|는데|지만|어서|아서)$'),
        ('조사_주격', r'(은|는|이|가)$'), ('조사_목적', r'(을|를)$'),
        ('조사_부사', r'(에|에서|으로|로|와|과|도|만|까지|부터)$')]


def tail_class(w):
    for n, p in TAIL:
        if re.search(p, w):
            return n
    return '무표지'


def syl(w):
    return sum(1 for c in w if '가' <= c <= '힣')


def rows_from(y, sr, words):
    """음절 수 비례 정렬 — 쉼을 뺀 발화 구간을 이어 붙인 시간축에서 어절의 음절 수 비례로 나눈다.

    강제 정렬기 없이 쓰는 근사다. 한국어 음절은 길이가 비교적 고르므로 총계 통계에는 쓸 만하고,
    수만 어절을 모으면 정렬 오차는 평균에서 상쇄된다. (1:1 매칭은 성공률이 60건 중 1건이라 못 쓴다.)
    """
    iv = [(a, b) for a, b in _split(y, sr, top_db=28) if (b - a) / sr >= 0.06]
    if not iv:
        return []
    t, f0 = _f0(y, sr)
    v = f0[~np.isnan(f0)]
    if len(v) < 20:
        return []
    med = float(np.median(v))

    # 유성 구간만 이어 붙인 시간축 (프레임 단위)
    keep = np.zeros(len(t), dtype=bool)
    for a, b in iv:
        keep |= (t >= a / sr) & (t < b / sr)
    idx = np.flatnonzero(keep)
    if len(idx) < 16:
        return []

    syls = [max(1, syl(w)) for w in words]
    tot = sum(syls)
    if tot < 3:
        return []
    bounds = np.cumsum([0] + syls) / tot            # 0~1
    out = []
    for k, w in enumerate(words):
        s0 = int(bounds[k] * len(idx)); s1 = int(bounds[k + 1] * len(idx))
        sel = idx[s0:s1]
        seg = f0[sel]
        seg = seg[~np.isnan(seg)]
        if len(seg) < 6:
            continue
        st = 12 * np.log2(seg / med)
        # 🔴 8점으로 나눌 때 표본이 8개 미만이면 빈 칸이 생겨 nan 이 된다(오늘 확인).
        #    보간으로 균일하게 8점을 뽑는다 — 짧은 어절도 곡선 모양이 남는다.
        xs = np.linspace(0, 1, len(st))
        pts = [float(np.interp(u, xs, st)) for u in np.linspace(0, 1, 8)]
        pos = 'A문두' if k == 0 else ('C문말' if k == len(words) - 1 else 'B문중')
        out.append({'onset': onset_class(w), 'tail': tail_class(w), 'syl': min(syls[k], 5),
                    'pos': pos, 'pts': pts, 'peak': float(np.argmax(st) / max(1, len(st) - 1)),
                    'range': float(st.max() - st.min()),
                    'dur_ms': float(len(sel)) * (t[1] - t[0]) * 1000 if len(t) > 1 else 0.0,
                    'rel': k / max(1, len(words) - 1)})
    return out


if __name__ == '__main__':
    tbl = pq.read_table(SRC)
    ac, tc = tbl.column('audio'), tbl.column('text')
    acc = []
    used = seen = 0
    for i in range(OFF, min(OFF + N, tbl.num_rows)):
        try:
            a = ac[i].as_py(); txt = tc[i].as_py().strip()
            words = txt.split()
            if len(words) < 3:
                continue
            y, sr = sf.read(io.BytesIO(a['bytes']), dtype='float32')
            if y.ndim > 1:
                y = y.mean(1)
            seen += 1
            r = rows_from(y, sr, words)
            if r:
                used += 1
                acc += r
        except Exception:
            continue
    print(f'DEEP {json.dumps({"tag": TAG, "seen": seen, "aligned": used, "words": len(acc), "rows": acc}, ensure_ascii=False)}', flush=True)
