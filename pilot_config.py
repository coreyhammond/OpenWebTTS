import os
from pathlib import Path


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


DATA_DIR = os.environ.get("OPENWEBTTS_DATA_DIR", "/data")
MODELS_DIR = str(Path(DATA_DIR) / "models")
PIPER_DIR = str(Path(MODELS_DIR) / "piper")
USERS_DIR = str(Path(DATA_DIR) / "users")
AUDIO_DIR = str(Path(DATA_DIR) / "audio")
AUDIO_CACHE_DIR = str(Path(DATA_DIR) / "audio_cache")
STATIC_DIR = "static"
TEMPLATES_DIR = "templates"

MAX_TEXT_CHARS = _bounded_int("OPENWEBTTS_MAX_TEXT_CHARS", 10_000, 1, 50_000)
MAX_UPLOAD_BYTES = _bounded_int(
    "OPENWEBTTS_MAX_UPLOAD_BYTES", 10 * 1024 * 1024, 1, 25 * 1024 * 1024
)
SYNTHESIS_TIMEOUT_SECONDS = _bounded_int(
    "OPENWEBTTS_SYNTHESIS_TIMEOUT_SECONDS", 120, 1, 300
)
DOWNLOAD_TIMEOUT_SECONDS = _bounded_int(
    "OPENWEBTTS_DOWNLOAD_TIMEOUT_SECONDS", 30, 1, 60
)
MODEL_DOWNLOAD_TIMEOUT_SECONDS = _bounded_int(
    "OPENWEBTTS_MODEL_DOWNLOAD_TIMEOUT_SECONDS", 180, 30, 600
)
DOCUMENT_TIMEOUT_SECONDS = _bounded_int(
    "OPENWEBTTS_DOCUMENT_TIMEOUT_SECONDS", 30, 5, 120
)
MAX_REQUEST_BODY_BYTES = MAX_UPLOAD_BYTES + 64 * 1024
SYNTHESIS_CONCURRENCY = _bounded_int(
    "OPENWEBTTS_SYNTHESIS_CONCURRENCY", 1, 1, 1
)


def ensure_data_directories() -> None:
    for path in (MODELS_DIR, PIPER_DIR, USERS_DIR, AUDIO_DIR, AUDIO_CACHE_DIR):
        Path(path).mkdir(parents=True, exist_ok=True)
