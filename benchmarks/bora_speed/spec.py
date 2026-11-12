"""Only public benchmark input and immutable experiment settings live here."""
MODEL_REPOSITORY = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
MODEL_REVISION = "0c0e3051f131929182e2c023b9537f8b1c68adfe"
NATIVE_REPOSITORY = "https://github.com/gabriele-mastrapasqua/qwen3-tts"
NATIVE_COMMIT = "ef339be58a778b062e1c14382347964552eae007"
PUBLIC_TEXT = "안녕하세요. 오늘도 차근차근 함께 공부해요."
INSTRUCTION = (
    "Speak Korean to one learner. Ask the questions with genuine curiosity, then explain "
    "with clear, conversational phrasing. Keep a consistent speaking voice throughout the "
    "paragraph. Allow occasional quiet, unforced inhalations and small thoughtful pauses "
    "at natural phrase boundaries when the meaning calls for them. Keep the vocal affect "
    "appropriate to the subject, and keep the spoken words clear and complete."
)
SEED = 5192
MAX_TOKENS = 160
PROFILE_SECONDS = 210
PROFILES = (
    {"id": "native_bf16_t4", "engine": "native", "dtype": "bf16", "threads": 4},
    {"id": "native_int8_t4", "engine": "native", "dtype": "int8", "threads": 4},
    {"id": "python_bf16_t2", "engine": "python", "dtype": "bf16", "threads": 2},
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
assert 0 < len(PUBLIC_TEXT) <= 40
