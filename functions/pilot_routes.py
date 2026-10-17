import asyncio
import hashlib
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from pilot_config import (
    AUDIO_CACHE_DIR,
    DOCUMENT_TIMEOUT_SECONDS,
    DOWNLOAD_TIMEOUT_SECONDS,
    MAX_TEXT_CHARS,
    MAX_UPLOAD_BYTES,
    MODEL_DOWNLOAD_TIMEOUT_SECONDS,
    PIPER_DIR,
    SYNTHESIS_CONCURRENCY,
    SYNTHESIS_TIMEOUT_SECONDS,
    TEMPLATES_DIR,
    ensure_data_directories,
)
from pilot_documents import parse_document_bounded
from pilot_security import (
    validate_download_redirect,
    validate_piper_download,
    validate_voice_key,
)


router = APIRouter()
templates = Jinja2Templates(directory=TEMPLATES_DIR)
SYNTHESIS_GATE = threading.BoundedSemaphore(SYNTHESIS_CONCURRENCY)
DOWNLOAD_GATE = threading.BoundedSemaphore(1)
DOCUMENT_GATE = threading.BoundedSemaphore(1)
MAX_MODEL_DOWNLOAD_BYTES = 200 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 64 * 1024


class SynthesizeRequest(BaseModel):
    engine: str
    voice: str
    text: str
    lang: Optional[str] = "en"


class PiperVoice(BaseModel):
    key: str
    URL: str


class Voice(BaseModel):
    id: str
    name: str


class DocumentText(BaseModel):
    text: str


def generate_piper_audio(
    model_path: str, text: str, output_path: str, timeout_seconds: int
) -> None:
    command = ["piper", "--model", model_path, "--output_file", output_path]
    subprocess.run(
        command,
        input=text,
        text=True,
        check=True,
        encoding="utf-8",
        timeout=timeout_seconds,
    )


async def read_upload_limited(file: UploadFile) -> bytes:
    content = bytearray()
    while True:
        chunk = await file.read(UPLOAD_CHUNK_BYTES)
        if not chunk:
            return bytes(content)
        content.extend(chunk)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Upload exceeds the configured limit.")


def _download_to_path(
    url: str, destination: Path, max_bytes: int, deadline: float
) -> None:
    temporary = destination.with_name(f".{destination.name}.part")
    total = 0
    try:
        current_url = url
        response = None
        for _ in range(5):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("model download timed out")
            response = requests.get(
                current_url,
                stream=True,
                timeout=(min(5, remaining), min(DOWNLOAD_TIMEOUT_SECONDS, remaining)),
                allow_redirects=False,
            )
            if response.status_code not in (301, 302, 303, 307, 308):
                break
            location = response.headers.get("location")
            response.close()
            if not location:
                raise ValueError("download redirect omitted its destination")
            current_url = validate_download_redirect(urljoin(current_url, location))
        else:
            raise ValueError("download exceeded the redirect limit")

        if response is None:
            raise ValueError("download returned no response")
        with response:
            response.raise_for_status()
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > max_bytes:
                raise ValueError("download exceeds the configured limit")
            with temporary.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("model download timed out")
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("download exceeds the configured limit")
                    handle.write(chunk)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _replace_voice_pair(
    staged_model: Path,
    staged_config: Path,
    model_path: Path,
    config_path: Path,
) -> None:
    backup_model = staged_model.with_name("previous.onnx")
    backup_config = staged_config.with_name("previous.onnx.json")
    try:
        if model_path.exists():
            model_path.replace(backup_model)
        if config_path.exists():
            config_path.replace(backup_config)
        staged_model.replace(model_path)
        staged_config.replace(config_path)
    except Exception:
        model_path.unlink(missing_ok=True)
        config_path.unlink(missing_ok=True)
        if backup_model.exists():
            backup_model.replace(model_path)
        if backup_config.exists():
            backup_config.replace(config_path)
        raise


@router.get("/healthz")
async def health() -> dict[str, str]:
    return {"status": "healthy", "engine": "piper"}


@router.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    ensure_data_directories()
    return templates.TemplateResponse(request=request, name="index.html")


@router.get("/config", response_class=HTMLResponse)
async def read_config(request: Request):
    return templates.TemplateResponse(request=request, name="config.html")


@router.get("/api/voices", response_model=list[Voice])
async def list_voices(engine: str):
    if engine != "piper":
        return []
    ensure_data_directories()
    return [
        Voice(id=path.stem, name=f"Piper: {path.stem}")
        for path in sorted(Path(PIPER_DIR).glob("*.onnx"))
    ]


@router.get("/api/piper_voices")
async def get_piper_voices_from_hf():
    try:
        response = requests.get(
            "https://huggingface.co/rhasspy/piper-voices/raw/main/voices.json",
            timeout=(5, DOWNLOAD_TIMEOUT_SECONDS),
            allow_redirects=False,
        )
        response.raise_for_status()
        return JSONResponse(content=response.json())
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail="Unable to retrieve Piper voices.") from exc


@router.post("/api/download_piper_voice")
async def download_piper_voice(voice: PiperVoice):
    try:
        key = validate_voice_key(voice.key)
        model_url = validate_piper_download(voice.URL)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    ensure_data_directories()
    model_path = Path(PIPER_DIR) / f"{key}.onnx"
    config_path = Path(PIPER_DIR) / f"{key}.onnx.json"
    if not DOWNLOAD_GATE.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="A model download is already running.")
    deadline = time.monotonic() + MODEL_DOWNLOAD_TIMEOUT_SECONDS
    try:
        with tempfile.TemporaryDirectory(prefix=f".{key}-", dir=PIPER_DIR) as staging:
            staged_model = Path(staging) / "voice.onnx"
            staged_config = Path(staging) / "voice.onnx.json"
            await asyncio.to_thread(
                _download_to_path,
                model_url,
                staged_model,
                MAX_MODEL_DOWNLOAD_BYTES,
                deadline,
            )
            await asyncio.to_thread(
                _download_to_path,
                f"{model_url}.json",
                staged_config,
                2 * 1024 * 1024,
                deadline,
            )
            await asyncio.to_thread(
                _replace_voice_pair,
                staged_model,
                staged_config,
                model_path,
                config_path,
            )
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Piper voice download timed out.") from exc
    except (requests.RequestException, OSError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Piper voice download failed.") from exc
    finally:
        DOWNLOAD_GATE.release()
    return {"message": f"Successfully downloaded {key}"}


@router.post("/api/synthesize")
async def synthesize_speech(request: SynthesizeRequest):
    if request.engine != "piper":
        raise HTTPException(status_code=403, detail="Only Piper is enabled in this pilot.")
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text cannot be empty.")
    if len(text) > MAX_TEXT_CHARS:
        raise HTTPException(status_code=413, detail="Text exceeds the configured limit.")
    try:
        voice = validate_voice_key(request.voice)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    ensure_data_directories()
    model_path = Path(PIPER_DIR) / f"{voice}.onnx"
    config_path = Path(PIPER_DIR) / f"{voice}.onnx.json"
    if not model_path.is_file() or not config_path.is_file():
        raise HTTPException(status_code=404, detail="Piper voice is not installed.")

    digest = hashlib.sha256(f"{voice}\0{text}".encode()).hexdigest()
    output_path = Path(AUDIO_CACHE_DIR) / f"{digest}.wav"
    if output_path.is_file():
        return {"audio_url": f"/generated/{output_path.name}", "status": "ready"}

    if not SYNTHESIS_GATE.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="A synthesis job is already running.")
    try:
        await asyncio.to_thread(
            generate_piper_audio,
            str(model_path),
            text,
            str(output_path),
            SYNTHESIS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        output_path.unlink(missing_ok=True)
        raise HTTPException(status_code=504, detail="Synthesis timed out.") from exc
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        output_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Synthesis failed.") from exc
    finally:
        SYNTHESIS_GATE.release()

    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise HTTPException(status_code=500, detail="Synthesis produced no audio.")
    return {"audio_url": f"/generated/{output_path.name}", "status": "ready"}


async def _read_document(
    file: UploadFile, kind: str, allowed_suffixes: tuple[str, ...]
) -> DocumentText:
    if not file.filename or not file.filename.lower().endswith(allowed_suffixes):
        raise HTTPException(status_code=400, detail=f"Invalid {kind.upper()} upload.")
    content = await read_upload_limited(file)
    if not DOCUMENT_GATE.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="A document is already being parsed.")
    try:
        text = await asyncio.to_thread(
            parse_document_bounded, kind, content, DOCUMENT_TIMEOUT_SECONDS
        )
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Document parsing timed out.") from exc
    except (EOFError, OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Unable to read {kind.upper()}.") from exc
    finally:
        DOCUMENT_GATE.release()
    if len(text) > MAX_TEXT_CHARS:
        raise HTTPException(status_code=413, detail="Document text exceeds the configured limit.")
    return DocumentText(text=text)


@router.post("/api/read_pdf", response_model=DocumentText)
async def read_pdf(file: UploadFile = File(...)):
    return await _read_document(file, "pdf", (".pdf",))


@router.post("/api/read_epub", response_model=DocumentText)
async def read_epub(file: UploadFile = File(...)):
    return await _read_document(file, "epub", (".epub", ".opf"))


@router.post("/api/read_docx", response_model=DocumentText)
async def read_docx(file: UploadFile = File(...)):
    return await _read_document(file, "docx", (".docx",))


@router.get("/api/cache_size")
async def cache_size():
    ensure_data_directories()
    size = sum(path.stat().st_size for path in Path(AUDIO_CACHE_DIR).glob("*") if path.is_file())
    return {"cache_size_mb": f"{size / (1024 * 1024):.2f} MB"}
