import socket

import pytest

from pilot_security import (
    is_public_address,
    validate_download_redirect,
    validate_piper_download,
    validate_voice_key,
)


def test_voice_key_rejects_path_traversal_and_accepts_piper_identifier():
    assert validate_voice_key("en_US-lessac-medium") == "en_US-lessac-medium"
    for value in ("../secret", "voice/../../secret", "", ".hidden", "voice name"):
        with pytest.raises(ValueError):
            validate_voice_key(value)


def test_piper_download_is_restricted_to_official_https_repository(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(None, None, None, None, ("13.224.10.2", 443))])
    url = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx"
    assert validate_piper_download(url) == url

    blocked = (
        "http://huggingface.co/rhasspy/piper-voices/resolve/main/model.onnx",
        "https://example.com/model.onnx",
        "https://huggingface.co/other/repository/model.onnx",
        "https://127.0.0.1/rhasspy/piper-voices/resolve/main/model.onnx",
        "https://169.254.169.254/latest/meta-data",
    )
    for candidate in blocked:
        with pytest.raises(ValueError):
            validate_piper_download(candidate)


def test_piper_download_rejects_paths_that_normalize_outside_official_repository(
    monkeypatch,
):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(None, None, None, None, ("13.224.10.2", 443))])

    traversal = "https://huggingface.co/rhasspy/piper-voices/resolve/main/../../../../attacker/repo/resolve/main/evil.onnx"
    encoded = "https://huggingface.co/rhasspy/piper-voices/resolve/main/%2e%2e/evil.onnx"
    for candidate in (traversal, encoded):
        with pytest.raises(ValueError, match="outside the official model repository"):
            validate_piper_download(candidate)


def test_address_policy_rejects_non_public_targets():
    assert is_public_address("8.8.8.8") is True
    for address in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fe80::1"):
        assert is_public_address(address) is False


def test_download_redirects_allow_only_public_hugging_face_delivery_hosts(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(None, None, None, None, ("13.224.10.2", 443))])

    assert validate_download_redirect("https://cdn-lfs.hf.co/file?signature=value")
    assert validate_download_redirect("https://cas-bridge.xethub.hf.co/file?signature=value")
    for value in (
        "http://cdn-lfs.hf.co/file",
        "https://example.com/file",
        "https://127.0.0.1/file",
    ):
        with pytest.raises(ValueError):
            validate_download_redirect(value)
