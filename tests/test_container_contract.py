from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_is_reproducible_multistage_and_non_root():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM node:" in dockerfile
    assert "AS frontend" in dockerfile
    assert "npm ci" in dockerfile
    assert "FROM python:" in dockerfile
    assert "pip install --require-hashes" in dockerfile
    assert "USER openwebtts" in dockerfile
    assert 'CMD ["python", "-c"' in dockerfile
    assert 'CMD ["python", "app.py", "--host", "0.0.0.0", "--port", "8000"]' in dockerfile
    assert "curl" not in dockerfile
    assert "ffmpeg" not in dockerfile
    assert "espeak-ng=1.52.0+dfsg-5" in dockerfile

    runtime_requirements = (ROOT / "requirements.in").read_text(encoding="utf-8")
    assert "pydub" not in runtime_requirements


def test_compose_has_no_host_port_and_declares_persistence():
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "ports:" not in compose
    assert "expose:" in compose
    assert '"8000"' in compose
    for path in ("/data/models", "/data/users", "/data/audio", "/data/audio_cache"):
        assert path in compose


def test_github_workflow_tests_smokes_and_publishes_amd64_image():
    workflow = (ROOT / ".github/workflows/private-pilot-image.yml").read_text(
        encoding="utf-8"
    )
    smoke = (ROOT / "scripts/container_smoke.sh").read_text(encoding="utf-8")

    assert "linux/amd64" in workflow
    assert "pytest" in workflow
    assert "scripts/container_smoke.sh" in workflow
    assert "/healthz" in smoke
    assert "ghcr.io" in workflow
    assert "docker tag openwebtts-smoke:${GITHUB_SHA}" in workflow
    assert "docker push" in workflow
    assert "docker/build-push-action" not in workflow


def test_container_smoke_precreates_nested_model_directory_for_cleanup():
    smoke = (ROOT / "scripts/container_smoke.sh").read_text(encoding="utf-8")

    assert 'mkdir -p "$root/models/piper"' in smoke
    assert 'chmod 0777 "$root/models/piper"' in smoke
