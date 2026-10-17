FROM node:22-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392 AS frontend

WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts
COPY static ./static
RUN npm run build

FROM python:3.11-slim-trixie@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce

ARG SOURCE_REVISION=unknown
LABEL org.opencontainers.image.source="https://github.com/coreyhammond/OpenWebTTS" \
      org.opencontainers.image.revision="${SOURCE_REVISION}" \
      org.opencontainers.image.title="OpenWebTTS private Piper pilot"

ENV OPENWEBTTS_DATA_DIR=/data \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        espeak-ng=1.52.0+dfsg-5 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 openwebtts \
    && useradd --uid 10001 --gid openwebtts --create-home --shell /usr/sbin/nologin openwebtts

WORKDIR /app
COPY requirements.lock ./
RUN python -m pip install --require-hashes --no-cache-dir -r requirements.lock \
    && python -m pip uninstall -y pip setuptools wheel

COPY --chown=openwebtts:openwebtts . .
COPY --from=frontend --chown=openwebtts:openwebtts /src/static ./static
RUN mkdir -p /data/models/piper /data/users /data/audio /data/audio_cache \
    && chown -R openwebtts:openwebtts /data /app

USER openwebtts
EXPOSE 8000
VOLUME ["/data/models", "/data/users", "/data/audio", "/data/audio_cache"]

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=4 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).read()"]

CMD ["python", "app.py", "--host", "0.0.0.0", "--port", "8000"]