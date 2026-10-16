import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def load_app(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("OPENWEBTTS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENWEBTTS_MAX_TEXT_CHARS", "20")
    monkeypatch.setenv("OPENWEBTTS_MAX_UPLOAD_BYTES", "32")
    for module_name in ("app", "pilot_config", "functions.pilot_routes"):
        sys.modules.pop(module_name, None)
    module = importlib.import_module("app")
    return module, TestClient(module.app)


def test_health_and_root_are_available(monkeypatch, tmp_path):
    _, client = load_app(monkeypatch, tmp_path)

    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json() == {"status": "healthy", "engine": "piper"}
    assert client.get("/").status_code == 200


@pytest.mark.parametrize(
    ("method", "path"),
    (
        ("post", "/api/users/create"),
        ("post", "/api/users/alice/books"),
        ("post", "/api/voice_cloning"),
        ("post", "/api/read_website"),
        ("post", "/api/speech_to_text"),
        ("get", "/v1/voices"),
        ("post", "/v1/audio/speech"),
        ("post", "/api/download_kokoro_voice"),
    ),
)
def test_disabled_surface_is_not_routable(monkeypatch, tmp_path, method, path):
    _, client = load_app(monkeypatch, tmp_path)
    response = getattr(client, method)(path)
    assert response.status_code == 404


def test_synthesis_accepts_only_bounded_piper_requests(monkeypatch, tmp_path):
    _, client = load_app(monkeypatch, tmp_path)

    non_piper = client.post(
        "/api/synthesize",
        json={"engine": "kokoro", "voice": "voice", "text": "hello"},
    )
    assert non_piper.status_code == 403

    oversized = client.post(
        "/api/synthesize",
        json={"engine": "piper", "voice": "voice", "text": "x" * 21},
    )
    assert oversized.status_code == 413

    traversal = client.post(
        "/api/synthesize",
        json={"engine": "piper", "voice": "../voice", "text": "hello"},
    )
    assert traversal.status_code == 400


def test_synthesis_returns_playable_wav_and_releases_gate(monkeypatch, tmp_path):
    module, client = load_app(monkeypatch, tmp_path)
    routes = importlib.import_module("functions.pilot_routes")
    model = Path(routes.PIPER_DIR) / "en_US-test-medium.onnx"
    model.write_bytes(b"model")
    model.with_suffix(".onnx.json").write_text("{}", encoding="utf-8")

    def fake_generate(model_path, text, output_path, timeout_seconds):
        assert model_path == str(model)
        assert text == "hello"
        assert timeout_seconds == routes.SYNTHESIS_TIMEOUT_SECONDS
        Path(output_path).write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")

    monkeypatch.setattr(routes, "generate_piper_audio", fake_generate)
    response = client.post(
        "/api/synthesize",
        json={"engine": "piper", "voice": "en_US-test-medium", "text": "hello"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    audio = client.get(body["audio_url"])
    assert audio.status_code == 200
    assert audio.content.startswith(b"RIFF")
    assert routes.SYNTHESIS_GATE.acquire(blocking=False) is True
    routes.SYNTHESIS_GATE.release()


def test_upload_limit_is_enforced_before_document_parsing(monkeypatch, tmp_path):
    _, client = load_app(monkeypatch, tmp_path)
    response = client.post(
        "/api/read_pdf",
        files={"file": ("sample.pdf", b"x" * 33, "application/pdf")},
    )
    assert response.status_code == 413


def test_request_body_limit_rejects_declared_oversize_before_parsing(monkeypatch, tmp_path):
    _, client = load_app(monkeypatch, tmp_path)
    response = client.post(
        "/api/read_pdf",
        content=b"small",
        headers={"content-type": "application/octet-stream", "content-length": "999999"},
    )
    assert response.status_code == 413


def test_model_download_concurrency_is_bounded(monkeypatch, tmp_path):
    _, client = load_app(monkeypatch, tmp_path)
    routes = importlib.import_module("functions.pilot_routes")
    assert routes.DOWNLOAD_GATE.acquire(blocking=False) is True
    try:
        response = client.post(
            "/api/download_piper_voice",
            json={
                "key": "en_US-test-low",
                "URL": "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/test/low/en_US-test-low.onnx",
            },
        )
        assert response.status_code == 429
    finally:
        routes.DOWNLOAD_GATE.release()


def test_model_download_uses_one_bounded_deadline(monkeypatch, tmp_path):
    _, client = load_app(monkeypatch, tmp_path)
    routes = importlib.import_module("functions.pilot_routes")
    deadlines = []

    monkeypatch.setattr(routes, "validate_piper_download", lambda value: value)

    def fake_download(url, destination, max_bytes, deadline):
        deadlines.append(deadline)
        Path(destination).write_bytes(b"model")

    monkeypatch.setattr(routes, "_download_to_path", fake_download)
    response = client.post(
        "/api/download_piper_voice",
        json={
            "key": "en_US-test-low",
            "URL": "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/test/low/en_US-test-low.onnx",
        },
    )

    assert response.status_code == 200
    assert len(deadlines) == 2
    assert deadlines[0] == deadlines[1]
    assert routes.time.monotonic() < deadlines[0]


def test_document_parsing_uses_bounded_worker_and_releases_gate(monkeypatch, tmp_path):
    _, client = load_app(monkeypatch, tmp_path)
    routes = importlib.import_module("functions.pilot_routes")
    calls = []

    def fake_parse(kind, content, timeout_seconds):
        calls.append((kind, content, timeout_seconds))
        return "bounded text"

    monkeypatch.setattr(routes, "parse_document_bounded", fake_parse)
    response = client.post(
        "/api/read_pdf",
        files={"file": ("sample.pdf", b"synthetic", "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json() == {"text": "bounded text"}
    assert calls == [("pdf", b"synthetic", routes.DOCUMENT_TIMEOUT_SECONDS)]
    assert routes.DOCUMENT_GATE.acquire(blocking=False) is True
    routes.DOCUMENT_GATE.release()


def test_document_parsing_concurrency_is_bounded(monkeypatch, tmp_path):
    _, client = load_app(monkeypatch, tmp_path)
    routes = importlib.import_module("functions.pilot_routes")
    assert routes.DOCUMENT_GATE.acquire(blocking=False) is True
    try:
        response = client.post(
            "/api/read_pdf",
            files={"file": ("sample.pdf", b"synthetic", "application/pdf")},
        )
        assert response.status_code == 429
    finally:
        routes.DOCUMENT_GATE.release()
