"""Only public benchmark input and immutable experiment settings live here."""
MODEL_REPOSITORY = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
MODEL_REVISION = "0c0e3051f131929182e2c023b9537f8b1c68adfe"
NATIVE_REPOSITORY = "https://github.com/gabriele-mastrapasqua/qwen3-tts"
NATIVE_COMMIT = "ef339be58a778b062e1c14382347964552eae007"
PUBLIC_TEXT = "오늘은 긴 문장을 이해하는 방법을 살펴보겠습니다. 먼저 누가 무엇을 하는지 찾아보세요. 주체와 행동을 확인한 다음에는 언제, 어떤 조건에서 그 행동을 하는지 연결하면 됩니다. 중요한 것은 내용을 짧은 덩어리로 나누는 것입니다. 예를 들어 지휘하는 사람과 지원하는 사람을 구분하고, 결정하는 단계와 실행하는 단계를 나누어 볼 수 있습니다. 이제 잠깐 멈추고, 방금 들은 내용을 자기 말로 설명해 보세요. 바로 떠오르지 않아도 괜찮습니다. 어려운 부분을 다시 확인하고, 한 문장으로 정리하면 됩니다. 마지막으로 오늘의 핵심을 기억하세요. 누가, 무엇을, 언제, 어떤 조건에서 하는지 차례로 확인하는 것입니다."
INSTRUCTION = (
    "Speak Korean to one learner. Ask the questions with genuine curiosity, then explain "
    "with clear, conversational phrasing. Keep a consistent speaking voice throughout the "
    "paragraph. Allow occasional quiet, unforced inhalations and small thoughtful pauses "
    "at natural phrase boundaries when the meaning calls for them. Keep the vocal affect "
    "appropriate to the subject, and keep the spoken words clear and complete."
)
SEED = 5192
MAX_TOKENS = 1100
PROFILE_SECONDS = 1050
PROFILES = (
    {"id": "native_bf16_t4", "engine": "native", "dtype": "bf16", "threads": 4},
    {"id": "native_int8_t4", "engine": "native", "dtype": "int8", "threads": 4},
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
assert len(PUBLIC_TEXT) == 338
