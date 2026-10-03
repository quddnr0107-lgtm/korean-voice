"""끝 「우웅」(배치 패딩 저음 · L280) 다시 굽기·다시 재기 — bake.py 가 쓰는 순수 함수 (2026-10-01).

왜 따로 두나 — bake.py 는 모델(onnx)을 올려야 돌아서 시험할 수 없다. 이 둘은 모델 없이 잰다(test/hum_test.py).

  고르기   다시뽑기(첫 소리, 다시뽑기, 우웅인가, 횟수) — 합성은 뽑기다(같은 글도 매번 다르다).
           한 번 더 굽고 끝내면 우웅이 남은 채 올라간다(2026-10-01 study 굽기 253 중 5).
           깨끗한 것이 나올 때까지 **횟수만큼** 다시 뽑는다. 끝까지 남으면 남았다고 돌려준다(올리는 것은 부르는 쪽이 정한다).
  다시 재기 mp3_소리(ffmpeg, mp3, sr) — 이미 R2 에 올라간 조각을 받아 같은 자(hum_tail)로 다시 잰다.
           로그의 조각이름으로 정확히 지정하면 그 조각만 재고, 지정하지 않으면 기존 범위를 잰다.
  조각표   조각이름(글) — 글을 안 드러내는 짧은 이름(sha1 앞 10자). 공개 로그에 남겨도 원고가 안 샌다.
"""
import hashlib
import re
import subprocess


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
    import numpy as np
    out = subprocess.run([ff, '-v', 'error', '-i', 'pipe:0', '-f', 'f32le', '-ac', '1', '-ar', str(int(sr)), 'pipe:1'],
                         input=mp3, check=True, capture_output=True).stdout
    return np.frombuffer(out, dtype=np.float32).copy()


def 조각이름(글):
    return hashlib.sha1(str(글).encode('utf-8')).hexdigest()[:10]


def target_ids(chunk_ids):
    """로그에 나온 정확한 10자리 ID만 받는다. 빈 값만 기존 전체 범위를 뜻한다."""
    if not chunk_ids.strip():
        return []
    ids = [value.strip() for value in chunk_ids.split(',')]
    if any(not re.fullmatch(r'[0-9a-f]{10}', value) for value in ids):
        raise ValueError('chunk_ids must be comma-separated exact 10-character lowercase hex IDs')
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate chunk_ids are not allowed')
    return ids


def validate_target_options(chunk_ids, start=0, limit=0, prune=False):
    """지정한 목록을 조용히 줄이거나, 나머지 음성을 삭제하지 못하게 한다."""
    if target_ids(chunk_ids) and (start != 0 or limit != 0 or prune):
        raise ValueError('chunk_ids requires start=0, limit=0 and prune=false')


def select_chunks(items, chunk_ids='', kind='all', law='all'):
    """목록 순서를 보존하며 범위 안에서 ID당 정확히 하나를 찾는다(모델·네트워크 불필요)."""
    ids = target_ids(chunk_ids)
    if not isinstance(items, list) or any(not isinstance(it, dict) for it in items):
        raise ValueError('chunks must be an array of objects')
    selected = list(items)
    for field, value in [('k', kind), ('w', law)]:
        if value != 'all':
            if any(field not in it for it in selected):
                raise ValueError(f'chunks missing scope field: {field}')
            selected = [it for it in selected if it.get(field) == value]
    if not ids:
        return selected
    matches = {chunk_id: [] for chunk_id in ids}
    for it in selected:
        if not isinstance(it.get('t'), str):
            raise ValueError('chunks missing text field')
        chunk_id = 조각이름(it['t'])
        if chunk_id in matches:
            matches[chunk_id].append(it)
    for chunk_id, found in matches.items():
        if len(found) != 1:
            raise ValueError(f'chunk_id {chunk_id}: expected exactly one match in scope, found {len(found)}')
    return [it for it in selected if 조각이름(it['t']) in matches]


def _main():
    """선택/계획용 진입점: 원고는 파일 안에만 두고 로그에는 ID·건수만 남긴다."""
    import argparse
    import json

    ap = argparse.ArgumentParser(description='Select exact logged chunk IDs without loading a voice model')
    ap.add_argument('--select-chunks', required=True)
    ap.add_argument('--out')
    ap.add_argument('--chunk-ids', default='')
    ap.add_argument('--kind', default='all', choices=['all', 'exam', 'easy', 'study'])
    ap.add_argument('--law', default='all')
    ap.add_argument('--start', type=int, default=0)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--prune', default='false', choices=['false', 'true'])
    args = ap.parse_args()
    try:
        validate_target_options(args.chunk_ids, args.start, args.limit, args.prune == 'true')
        with open(args.select_chunks, encoding='utf-8') as source:
            items = json.load(source)
        selected = select_chunks(items, args.chunk_ids, args.kind, args.law)
    except ValueError as exc:
        ap.error(str(exc))
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as output:
            json.dump(selected, output, ensure_ascii=False)
    print(json.dumps({'source_count': len(items), 'selected_count': len(selected),
                      'chunk_ids': target_ids(args.chunk_ids)}, ensure_ascii=False))


if __name__ == '__main__':
    _main()
