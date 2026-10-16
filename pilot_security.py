import ipaddress
import re
import socket
from urllib.parse import urlparse


VOICE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
PIPER_HOST = "huggingface.co"
PIPER_PATH_PREFIX = "/rhasspy/piper-voices/resolve/main/"
DOWNLOAD_HOST_SUFFIXES = (".hf.co", ".huggingface.co")


def is_public_address(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_global
    except ValueError:
        return False


def validate_voice_key(value: str) -> str:
    if not VOICE_KEY_PATTERN.fullmatch(value):
        raise ValueError("invalid Piper voice key")
    return value


def _resolved_public_addresses(hostname: str) -> list[str]:
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        records = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        addresses = sorted({str(record[4][0]) for record in records})
    else:
        addresses = [str(literal)]

    if not addresses or any(not is_public_address(address) for address in addresses):
        raise ValueError("download target must resolve only to public addresses")
    return addresses


def validate_piper_download(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != PIPER_HOST:
        raise ValueError("Piper downloads must use the official HTTPS repository")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("Piper download URL contains forbidden authority fields")
    if not parsed.path.startswith(PIPER_PATH_PREFIX) or not parsed.path.endswith(".onnx"):
        raise ValueError("Piper download path is outside the official model repository")
    if parsed.query or parsed.fragment:
        raise ValueError("Piper download URL must not contain query or fragment data")
    _resolved_public_addresses(parsed.hostname)
    return url


def validate_download_redirect(url: str) -> str:
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    allowed_host = hostname == PIPER_HOST or any(
        hostname.endswith(suffix) for suffix in DOWNLOAD_HOST_SUFFIXES
    )
    if parsed.scheme != "https" or not allowed_host:
        raise ValueError("download redirect left the approved HTTPS host set")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("download redirect contains forbidden authority fields")
    _resolved_public_addresses(hostname)
    return url
