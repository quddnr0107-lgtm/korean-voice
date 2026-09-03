# 예제 — 대본과 톤 프로필

같은 안내문을 세 가지 말하기 방식으로. 톤은 `--profile 톤_*.json`, 목소리는 `--style`, 밝기·펀치는 `--bright-db`·`--punch`.

| 방식 | 대본 | 톤 | 권장 옵션 |
|---|---|---|---|
| 따뜻한 안내 | 대본_안내문_따뜻.txt | public/profiles/owner.json(본인 말투) | `--style F1:0.6,F3:0.4` |
| 짜증·강조 | 대본_안내문_짜증강조.txt | 톤_짜증강조.json | `--style M1:0.6,F4:0.4 --base-speed 1.15 --bright-db 4 --punch 0.5` |
| 일타강사 | 대본_안내문_일타강사.txt | 톤_일타강사.json | `--style M1:0.7,M3:0.3 --base-speed 1.2 --bright-db 3 --punch 0.6` |

일타강사 화법의 핵심은 대본에 있다: "자!"로 끊기, 핵심 앞 `<break time="0.4s"/>`, 되묻기("됐죠?"), 숫자 반복("다시! 육군 18…"), 짧은 문장.
