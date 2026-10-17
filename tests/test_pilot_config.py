import importlib

import pytest


def load_config(monkeypatch, **environment):
    keys = {
        "OPENWEBTTS_DATA_DIR",
        "OPENWEBTTS_MAX_TEXT_CHARS",
        "OPENWEBTTS_MAX_UPLOAD_BYTES",
        "OPENWEBTTS_SYNTHESIS_TIMEOUT_SECONDS",
        "OPENWEBTTS_DOWNLOAD_TIMEOUT_SECONDS",
        "OPENWEBTTS_MODEL_DOWNLOAD_TIMEOUT_SECONDS",
        "OPENWEBTTS_DOCUMENT_TIMEOUT_SECONDS",
        "OPENWEBTTS_SYNTHESIS_CONCURRENCY",
    }
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    for key, value in environment.items():
        monkeypatch.setenv(key, str(value))

    import pilot_config

    return importlib.reload(pilot_config)


def test_private_pilot_defaults_are_bounded_and_use_one_persistent_root(monkeypatch):
    config = load_config(monkeypatch)

    assert config.DATA_DIR == "/data"
    assert config.MODELS_DIR == "/data/models"
    assert config.USERS_DIR == "/data/users"
    assert config.AUDIO_DIR == "/data/audio"
    assert config.AUDIO_CACHE_DIR == "/data/audio_cache"
    assert config.MAX_TEXT_CHARS == 10_000
    assert config.MAX_UPLOAD_BYTES == 10 * 1024 * 1024
    assert config.SYNTHESIS_TIMEOUT_SECONDS == 120
    assert config.DOWNLOAD_TIMEOUT_SECONDS == 30
    assert config.MODEL_DOWNLOAD_TIMEOUT_SECONDS == 180
    assert config.DOCUMENT_TIMEOUT_SECONDS == 30
    assert config.SYNTHESIS_CONCURRENCY == 1


def test_invalid_or_unbounded_numeric_configuration_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="OPENWEBTTS_MAX_TEXT_CHARS"):
        load_config(monkeypatch, OPENWEBTTS_MAX_TEXT_CHARS="0")

    with pytest.raises(ValueError, match="OPENWEBTTS_SYNTHESIS_CONCURRENCY"):
        load_config(monkeypatch, OPENWEBTTS_SYNTHESIS_CONCURRENCY="3")
