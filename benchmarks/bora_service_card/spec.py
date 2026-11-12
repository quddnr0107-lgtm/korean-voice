"""Only public benchmark input and immutable experiment settings live here."""
import os
MODEL_REPOSITORY = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
MODEL_REVISION = "0c0e3051f131929182e2c023b9537f8b1c68adfe"
PARTS = (
    "오늘은 병역법 제팔십삼조의 삼, 병역증과 전역증의 소지 의무를 살펴보겠습니다. 먼저 질문 하나 할게요. 병역 의무자는 평소에도 병역증을 항상 가지고 다녀야 할까요? 조문을 읽어 보겠습니다. 전시, 사변이나 동원령이 선포되면, 병역 의무자는 병역증 또는 전역증을 지니고 다녀야 한다.",
    "먼저 언제 지녀야 하는지 보세요. 전시, 사변이나 동원령이 선포되면이라는 조건이 붙어 있습니다. 따라서 이 조문을 병역 의무자는 평소에도 언제나 병역증을 지녀야 한다고 바꾸면 틀립니다. 또 조문에는 통합방위사태가 아니라 동원령이라고 적혀 있습니다.",
    "다음은 누가, 무엇을 지니는지입니다. 주체는 병역 의무자입니다. 모든 국민이라고 범위를 넓히지 마세요. 지녀야 하는 것은 병역증 또는 전역증입니다. 여기서 또는이라는 말이 중요합니다. 병역증과 전역증을 둘 다 지니라는 뜻으로 읽으면 안 됩니다. 문장의 끝도 확인해 보세요. 단순히 집에 보관하는 것이 아니라, 지니고 다녀야 한다고 규정하고 있습니다.",
    "이제 짧게 확인해 볼게요. 병역 의무자는 평소에도 두 증서를 항상 함께 지녀야 한다. 맞을까요? 틀립니다. 시기와 증서의 연결 방식이 모두 달라졌습니다. 그럼 정확한 문장을 다시 들어 보세요. 전시, 사변이나 동원령이 선포되면, 병역 의무자는 병역증 또는 전역증을 지니고 다녀야 한다. 언제, 누가, 무엇을, 어떻게. 이 네 자리를 연결해서 기억하세요.",
)
_part = os.environ["BORA_SAMPLE_PART"]
if _part not in ("0", "1", "2", "3"):
    raise ValueError("BORA_SAMPLE_PART must be 0, 1, 2, or 3")
SAMPLE_PART = int(_part)
PUBLIC_TEXT = PARTS[SAMPLE_PART]
INSTRUCTION = (
    "Speak Korean to one learner. Ask the questions with genuine curiosity, then explain "
    "with clear, conversational phrasing. Keep a consistent speaking voice throughout the "
    "paragraph. Allow occasional quiet, unforced inhalations and small thoughtful pauses "
    "at natural phrase boundaries when the meaning calls for them. Keep the vocal affect "
    "appropriate to the subject, and keep the spoken words clear and complete."
)
SEED = 5192
MAX_TOKENS = 1100
PROFILE_SECONDS = 750
PROFILES = (
    {"id": "python_fp32_t4", "engine": "python", "dtype": "fp32", "threads": 4},
)
PACKAGES = {
    "torch": "2.8.0+cpu", "torchaudio": "2.8.0+cpu", "qwen-tts": "0.1.1",
    "transformers": "4.57.3", "faster-whisper": "1.2.1", "soundfile": "0.14.0",
    "cryptography": "50.0.0", "numpy": "2.5.4", "scipy": "1.18.1",
    "onnxruntime": "1.31.1",
}
MODEL_FILES = {
    "model.safetensors": 3833402552,
    "speech_tokenizer/model.safetensors": 682293092,
    "config.json": None, "generation_config.json": None,
    "tokenizer_config.json": None, "preprocessor_config.json": None,
    "vocab.json": None, "merges.txt": None,
    "speech_tokenizer/config.json": None,
    "speech_tokenizer/configuration.json": None,
    "speech_tokenizer/preprocessor_config.json": None,
}
assert len(PARTS) == 4 and all(0 < len(part) <= 500 for part in PARTS)
