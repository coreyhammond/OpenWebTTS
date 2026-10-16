#!/usr/bin/env bash
set -euo pipefail

image=${1:?usage: container_smoke.sh IMAGE}
name="openwebtts-smoke-${RANDOM}"
root=$(mktemp -d)
container_id=""

cleanup() {
  if [[ -n "$container_id" ]]; then
    docker rm -f "$container_id" >/dev/null 2>&1 || true
  fi
  rm -rf "$root"
}
trap cleanup EXIT

for directory in models users audio audio_cache; do
  mkdir -p "$root/$directory"
  chmod 0777 "$root/$directory"
done

start_container() {
  container_id=$(docker run -d \
    --name "$name" \
    --platform linux/amd64 \
    --read-only \
    --tmpfs /tmp:size=64m,mode=1777 \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    -p 127.0.0.1:18000:8000 \
    -v "$root/models:/data/models" \
    -v "$root/users:/data/users" \
    -v "$root/audio:/data/audio" \
    -v "$root/audio_cache:/data/audio_cache" \
    "$image")
}

wait_healthy() {
  for _ in $(seq 1 60); do
    status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id")
    if [[ "$status" == "healthy" ]]; then
      return 0
    fi
    if [[ "$status" == "unhealthy" ]]; then
      docker logs "$container_id"
      return 1
    fi
    sleep 2
  done
  docker logs "$container_id"
  return 1
}

start_container
wait_healthy

[[ $(docker inspect --format '{{.Config.User}}' "$container_id") == "openwebtts" ]]
curl --fail --silent http://127.0.0.1:18000/healthz | grep -q '"status":"healthy"'

for endpoint in \
  /api/users/create \
  /api/voice_cloning \
  /api/read_website \
  /api/speech_to_text \
  /v1/voices; do
  code=$(curl --silent --output /dev/null --write-out '%{http_code}' "http://127.0.0.1:18000${endpoint}")
  [[ "$code" == "404" ]]
done

voice_key="en_US-lessac-low"
voice_url="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/low/en_US-lessac-low.onnx"
curl --fail --silent \
  --header 'content-type: application/json' \
  --data "{\"key\":\"${voice_key}\",\"URL\":\"${voice_url}\"}" \
  http://127.0.0.1:18000/api/download_piper_voice >/dev/null

synthesis=$(curl --fail --silent \
  --header 'content-type: application/json' \
  --data "{\"engine\":\"piper\",\"voice\":\"${voice_key}\",\"text\":\"OpenWebTTS pilot smoke.\"}" \
  http://127.0.0.1:18000/api/synthesize)
audio_url=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["audio_url"])' <<<"$synthesis")
curl --fail --silent "http://127.0.0.1:18000${audio_url}" --output "$root/sample.wav"
python3 - "$root/sample.wav" <<'PY'
import sys
import wave

with wave.open(sys.argv[1], "rb") as sample:
    assert sample.getnframes() > 0
    assert sample.getframerate() > 0
PY

for directory in models users audio audio_cache; do
  docker exec "$container_id" sh -c "printf persistent > /data/${directory}/persistence-marker"
done

docker rm -f "$container_id" >/dev/null
container_id=""
start_container
wait_healthy
for directory in models users audio audio_cache; do
  [[ $(docker exec "$container_id" cat "/data/${directory}/persistence-marker") == "persistent" ]]
done

if docker logs "$container_id" 2>&1 | grep -Fq 'OpenWebTTS pilot smoke.'; then
  echo "Synthetic document text appeared in logs" >&2
  exit 1
fi
