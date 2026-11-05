# 쉬운술술성경 여성 B 운영 및 복구

## 확인된 상태 — 2026-10-22 UTC

이 문서는 수정본의 동작과 실제 원격 증거를 구분한다. GitHub에 이 수정본이 반영되었다는 뜻은 아니다. 적용 커밋과 CI 결과는 별도로 확인해야 한다.

- 원본 PR: <https://github.com/quddnr0107-lgtm/korean-voice/pull/77>, 기준 커밋 `bce2be53f45c21938e8e959775c462ea869f0d83`.
- 실제 생성 실행: <https://github.com/quddnr0107-lgtm/korean-voice/actions/runs/39526515759>.
- 원격 생성은 2026-10-21 01:18:11–03:31:10 UTC에 완료됐다. plan 1개와 bake 95개 모두 성공했다.
- 66개 공개 prerelease에 장별 ZIP 1,189개가 모두 `uploaded` 상태다. URL·바이트 수·GitHub SHA-256을 전수 고정했다. 전체 ZIP 크기는 6,162,955,606 바이트다.
- ZIP 하나는 장의 여러 텍스트 단위 MP3와 `manifest.json`을 담는다. “장당 MP3 한 개” 형식이 아니다.
- corpus는 61,550개의 전역 중복 제거 키를 가진다. 다른 장과 같은 문장은 한 소유 장 ZIP에만 저장된다. Android는 본문 키를 기준으로 필요한 소유 장 ZIP을 모두 확보해야 한다.
- 현재 누락 장 0, 실패 생성 작업 0, 재시도 대상 0이다. 이 복구 작업에서 합성 또는 원격 업로드를 실행하지 않았다.

`qa/20261022/release-snapshot.json`, `run-metadata.json`, `run-jobs.json`은 GitHub 읽기 응답에서 만든 증거다. `release-catalog.json`은 그 목록과 corpus를 대응시킨 배포 계약이다. `inventory-check.json`은 실제 조회 스냅샷에 인벤토리 코드를 적용한 결과이며, 로컬 gh CLI로 라이브 검사를 실행했다는 뜻은 아니다.

## 고정한 스타일

기존 F4 60% + F2 40%, calm, 합성 기본 속도 1.06, steps 16, gain −2 dB, profile 없음, 기존 모델·engine·approvedSample을 유지했다. `render`와 `load_engine` 함수의 AST가 원본 커밋과 동일한 증거는 `style-preservation.json`에 있다. 이 완료 판본의 CLI에는 새 합성 경로가 없다.

`assets.py`는 모델 LFS SHA-256뿐 아니라 F2/F4 JSON의 Git blob SHA-1 및 크기도 검증한다. 현재 복구 workflow는 모델을 다운로드하거나 ONNX를 실행하지 않는다.

## 수정본 적용 후 기본 동작

push, pull_request, 기본 workflow_dispatch는 **audit만** 실행한다. 누락 파일이 발견되어도 자동 합성·업로드하지 않는다. 복구는 사용자가 명시적으로 `retry-missing` 또는 `retry-failed`를 선택한 경우에만 진행한다.

원격 API 실패, 403/429, 네트워크 오류, 잘못된 JSON, digest·크기 불일치는 “파일 없음”으로 해석하지 않고 중단한다. 누락으로 인정하는 경우는 성공한 전체 목록에서 자산이 없거나 정확한 404 응답을 받은 경우다. 시편 등 큰 릴리스는 자산 페이지를 끝까지 조회한다.

이미 공개된 자산은 상태·URL·크기·SHA-256이 고정 목록과 일치해야 완료로 판단한다. 불일치한 원격 파일을 자동 삭제하거나 덮어쓰지 않는다. 업로드에 `--clobber`를 쓰지 않는다.

### 인벤토리 확인

GitHub CLI `gh`와 읽기 권한이 있는 토큰을 준비한 환경에서:

```sh
python3 sulsul/bake.py --audit --report sulsul-inventory.json
python3 sulsul/bake.py --matrix
```

정상 상태의 결과는 complete 1,189, missing 빈 목록, matrix.job 빈 목록이다.

### 실패 작업만 복구

`retry-failed`는 지정 실행이 완료된 Sulsul workflow인지 확인한다. 그 실행의 최신 job 상태 중 실패·취소·시간 초과 bake ID만 선택하고, 현재 누락된 장을 가진 job과 교집합을 구한다. 선택한 job이 이미 완료되었으면 실행하지 않는다. optional `job_ids`로 더 좁힐 수 있다.

```sh
python3 sulsul/bake.py --matrix --mode retry-failed --failed-run-id RUN_ID --job-ids 014,015
python3 sulsul/bake.py --job 014 --mode retry-failed --failed-run-id RUN_ID --job-ids 014 --publish
```

`RUN_ID`는 실제 실패 실행 번호로 바꾼다. 현재 성공 실행을 지정하면 복구할 실패 job은 없다.

### 반드시 원본 ZIP 바이트가 필요하다

현재 음성은 모두 공개 완료됐고 Android에 SHA-256을 고정했으므로, 없어진 자산은 **같은 원본 ZIP**으로만 복원한다. 같은 MP3를 다시 ZIP으로 묶어도 ZIP 시간·헤더 차이로 SHA-256이 바뀔 수 있으므로 거부한다. 재합성도 하지 않는다.

로컬 원본 보관 위치는 `sulsul-output/25326e55be85/GEN-1.zip`처럼 `<BOOK>-<CHAPTER>.zip`이다. archive 전체 SHA-256·크기, ZIP 항목 집합/CRC, manifest schema/version/recipe/bundle/키 집합, 개별 MP3 SHA-256·크기·길이 메타데이터를 확인한 뒤에만 올린다. 원본 ZIP이 없으면 명시적으로 실패하고 기존 완료 파일은 보존한다.

Actions 복구는 `failed_run_id`가 지정되면 그 실행의 해당 job 전용 `sulsul-checkpoint-014` artifact를 가져온다. 실패 artifact는 3일 보존이다. 이전 생성 실행에는 이 checkpoint 단계가 없었으므로, 과거 ZIP이 손실됐다면 현재 공개 원본이나 별도 원본 백업이 필요하다. 없는 artifact나 원본을 있다고 가정하지 않는다. 현재 원격 자산은 모두 존재하므로 복원할 것이 없다.

업로드 응답이 끊기거나 실패 코드가 와도 원격 상태를 다시 확인한다. 동일한 자산이 올라갔으면 성공으로 처리하고, 없으면 원본 checkpoint를 남긴 채 실패한다. 다음 복구에서도 완료 자산을 먼저 확인한다.

## 실제 자원 사용과 실행 한도

| 항목 | 관찰/계산 결과 |
| --- | ---: |
| bake 작업 | 95개 성공 |
| 누적 bake runner 시간 | 3,639.30분 = 60.655시간 |
| 전체 실행 경과 | 132.983분 = 2시간 12분 59초 |
| 작업 평균 / 중앙값 | 38.31 / 42.33분 |
| 작업 p95 / 최대 | 47.22 / 47.78분 |
| 실제 최대 동시 실행 | 32개 |
| 작업 timeout / soft budget | 350 / 320분 |
| 95개 모두 timeout까지 도는 산술 상한 | 33,250 runner분 = 554.17시간 |
| 모델 1회 cold download | 398,945,433바이트 |
| 95개 모두 cold일 때 모델 전송 상한 | 37,899,816,135바이트 ≈ 35.30GiB |
| 완료 장 ZIP 전체 | 6,162,955,606바이트 ≈ 5.74GiB |

누적 runner 시간은 실제 job 시작·종료 시각의 합계이고 청구서가 아니다. cold 다운로드 상한은 가정 계산이며 실제 cache hit나 전송량을 측정한 값이 아니다. 작업별 CPU·최대 메모리는 이번 API 데이터로 측정하지 못했다. 공개 저장소의 표준 GitHub hosted runner 무료 정책과 계정별 동시 실행 제한은 [GitHub 공식 문서](https://docs.github.com/en/actions/concepts/billing-and-usage) 및 [Actions limits](https://docs.github.com/en/actions/reference/limits)를 따른다. 업그레이드된 runner나 다른 저장소의 비용까지 0원이라고 추론하지 않는다.

350분은 GitHub hosted job의 6시간 한도 아래다. 32개 설정은 계정별 실행 허용량을 보장하지 않는다. API 조회 및 업로드는 repository token의 시간당 요청 한도를 공유하므로 대규모 복구를 한꺼번에 강행하지 말고 필요한 job만 선택한다. rate limit이 나면 실패하도록 두고 완료 자산을 보존한다. 이 복구 workflow에는 합성 모델 실행이 없어 원래 95개 생성 비용을 다시 쓰지 않는다.

## 검증과 남은 출시 조건

```sh
python3 -m unittest discover -s sulsul -p 'test_*.py'
python3 sulsul/build_catalog.py sulsul/qa/20261022/release-snapshot.json
```

31개 자동 테스트가 API 실패 처리, 404, 고정 파일 SHA, 누락 원본 거부, ZIP 구성/manifest/MP3 검증, 완료 장 skip, 명시적 publish, 실패 ID 교집합, 시편 pagination, lost upload 응답 재확인, 전체 목록 완전성을 검사한다. `render`를 실제 실행하는 테스트는 없고 음성을 재생성하지 않는다.

릴리스 전에는 대표 실제 MP3의 한국어 발음·고유명사·문장 연결·쉼표와 마침표 호흡·장시간 피로도를 사람이 듣고 승인해야 한다. 파일 decode 성공만으로 청취 품질을 승인하지 않는다. Android 실기기 다운로드·재시작·비행기 모드·초 단위 이어 듣기·중단 복구·저장 공간 부족도 별도 통과가 필요하다. 기존 구독 검증은 오프라인 재시작 때 유료 본문 접근을 잠글 수 있으므로, 신뢰 가능한 만료 정보와 오프라인 권한 정책을 마련하기 전에는 유료 본문 전체 오프라인 지원을 완료라고 표기하지 않는다.
