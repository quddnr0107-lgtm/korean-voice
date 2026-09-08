"""조합 등록부 — 사이트가 목소리를 두 벌 동시에 내준다(2026-09-08 사용자 「음성 2개를 고를수있게」).

  u5  지금 목소리   F4:0.6+F2:0.4 · Praat PSOLA 억양       스텝 8
  k2  옛 목소리     F2 순수 · 실측 억양 궤적 · 어절 층      스텝 16

🔴 스텝이 조합에 딸려 있다 — 캐시 키가 `voice|steps|r|표식|글` 이라, 표식만 바꾸고 스텝을 안 맞추면
   이미 구워 둔 벌을 통째로 못 찾는다(실측: k2 를 스텝 8 로 찾으면 전량 miss).
   lib/tts-key.mjs 의 TAGS 와 글자·숫자까지 같아야 한다(test/tts-key.test.mjs 가 잰다).
"""
import voice_shape
import voice_shape_k2

TAGS = {
    voice_shape.RECIPE_TAG:    {'mod': voice_shape,    'steps': 8},
    voice_shape_k2.RECIPE_TAG: {'mod': voice_shape_k2, 'steps': 16},
}
DEFAULT = voice_shape.RECIPE_TAG


def get(tag):
    """모르는 표식이면 기본 조합을 준다(밖에서 온 값이므로 믿지 않는다)."""
    return TAGS.get(tag or DEFAULT, TAGS[DEFAULT])['mod']


def steps_for(tag):
    return TAGS.get(tag or DEFAULT, TAGS[DEFAULT])['steps']
