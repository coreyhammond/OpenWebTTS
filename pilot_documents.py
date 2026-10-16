import multiprocessing
import os
import tempfile
from io import BytesIO
from pathlib import Path


MEMORY_LIMIT_BYTES = 512 * 1024 * 1024
CPU_LIMIT_SECONDS = 20


def _apply_limits() -> None:
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
        resource.setrlimit(resource.RLIMIT_CPU, (CPU_LIMIT_SECONDS, CPU_LIMIT_SECONDS))
    except (ImportError, OSError, ValueError):
        pass


def _parse(kind: str, content: bytes) -> str:
    if kind == "pdf":
        import pymupdf

        document = pymupdf.open(stream=content, filetype="pdf")
        return "".join(page.get_text() for page in document)
    if kind == "docx":
        import docx

        document = docx.Document(BytesIO(content))
        return "\n".join(paragraph.text for paragraph in document.paragraphs)
    if kind == "epub":
        from bs4 import BeautifulSoup
        from ebooklib import ITEM_DOCUMENT, epub

        path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".epub") as handle:
                handle.write(content)
                path = handle.name
            book = epub.read_epub(path)
            return "\n".join(
                BeautifulSoup(item.content, "html.parser").get_text(" ")
                for item in book.get_items()
                if item.get_type() == ITEM_DOCUMENT
            )
        finally:
            if path:
                Path(path).unlink(missing_ok=True)
    raise ValueError("unsupported document type")


def _worker(connection, kind: str, content: bytes) -> None:
    _apply_limits()
    try:
        connection.send((True, _parse(kind, content)))
    except Exception:
        connection.send((False, "document parsing failed"))
    finally:
        connection.close()


def parse_document_bounded(kind: str, content: bytes, timeout_seconds: int) -> str:
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(child, kind, content), daemon=True)
    process.start()
    child.close()
    try:
        if not parent.poll(timeout_seconds):
            process.terminate()
            process.join(2)
            if process.is_alive():
                process.kill()
                process.join(2)
            raise TimeoutError("document parsing timed out")
        success, result = parent.recv()
        process.join(2)
        if not success:
            raise ValueError(result)
        return result
    finally:
        parent.close()
        if process.is_alive():
            process.terminate()
            process.join(2)
