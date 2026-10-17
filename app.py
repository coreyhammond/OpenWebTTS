import argparse
import socket
import threading
import time

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from functions.pilot_routes import router
from pilot_config import AUDIO_CACHE_DIR, MAX_REQUEST_BODY_BYTES, STATIC_DIR, ensure_data_directories
from pilot_middleware import RequestBodyLimitMiddleware


ensure_data_directories()
app = FastAPI(
    title="OpenWebTTS private Piper pilot",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(RequestBodyLimitMiddleware, max_bytes=MAX_REQUEST_BODY_BYTES)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/generated", StaticFiles(directory=AUDIO_CACHE_DIR), name="generated")
app.include_router(router)


def _find_free_port(preferred_port: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", preferred_port))
            return preferred_port
        except OSError:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OpenWebTTS private Piper pilot")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--app", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    host = args.host
    port = _find_free_port(args.port)
    if not args.app:
        uvicorn.run(app, host=host, port=port)
    else:
        try:
            import webview
        except ImportError as exc:
            raise RuntimeError("pywebview is required for desktop mode") from exc

        server_config = uvicorn.Config(app, host=host, port=port, log_level="info")
        server = uvicorn.Server(server_config)
        server_thread = threading.Thread(target=server.run, daemon=True)
        server_thread.start()
        for _ in range(50):
            if getattr(server, "started", False):
                break
            time.sleep(0.1)
        try:
            webview.create_window(
                "OpenWebTTS", f"http://{host}:{port}", width=1280, height=720
            )
            webview.start(debug=args.debug, private_mode=False)
        finally:
            server.should_exit = True
            time.sleep(0.2)