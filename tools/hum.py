"""끝 「우웅」(배치 패딩 저음 · L280) 다시 굽기·다시 재기 — bake.py 가 쓰는 순수 함수 (2026-10-01).

왜 따로 두나 — bake.py 는 모델(onnx)을 올려야 돌아서 시험할 수 없다. 이 둘은 모델 없이 잰다(test/hum_test.py).

  고르기   다시뽑기(첫 소리, 다시뽑기, 우웅인가, 횟수) — 합성은 뽑기다(같은 글도 매번 다르다).
           한 번 더 굽고 끝내면 우웅이 남은 채 올라간다(2026-10-01 study 굽기 253 중 5).
           깨끗한 것이 나올 때까지 **횟수만큼** 다시 뽑는다. 끝까지 남으면 남았다고 돌려준다(올리는 것은 부르는 쪽이 정한다).
  다시 재기 mp3_소리(ffmpeg, mp3, sr) — 이미 R2 에 올라간 조각을 받아 같은 자(hum_tail)로 다시 잰다.
           로그는 글을 가리므로(비공개 원고) **어느 조각인지 모른다** — 그래서 전부 다시 재서 찾는다.
  조각표   조각이름(글) — 글을 안 드러내는 짧은 이름(sha1 앞 10자). 공개 로그에 남겨도 원고가 안 샌다.
"""
import hashlib
import subprocess

import numpy as np


def 다시뽑기(첫소리, 다시뽑기_함수, 우웅인가, 횟수):
    """(소리, 다시 뽑은 횟수, 그래도 우웅) — 우웅인가 가 None(못 잼)이면 통과로 세지 않고 그대로 둔다(R139: 못 잰 것은 잰 것이 아니다)."""
    y = 첫소리
    if 우웅인가(y) is not True:
        return y, 0, False
    n = 0
    for _ in range(max(0, int(횟수))):
        y = 다시뽑기_함수(); n += 1
        if 우웅인가(y) is not True:
            return y, n, False
    return y, n, True


def mp3_소리(ff, mp3, sr):
    """mp3 바이트 → 모노 float32(sr). ffmpeg 가 실패하면 예외(조용히 빈 소리로 넘어가지 않는다)."""
    out = subprocess.run([ff, '-v', 'error', '-i', 'pipe:0', '-f', 'f32le', '-ac', '1', '-ar', str(int(sr)), 'pipe:1'],
                         input=mp3, check=True, capture_output=True).stdout
    return np.frombuffer(out, dtype=np.float32).copy()


def 조각이름(글):
    return hashlib.sha1(str(글).encode('utf-8')).hexdigest()[:10]
