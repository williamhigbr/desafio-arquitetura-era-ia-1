#!/bin/bash
# Entrypoint do gateway: sobe o LiteLLM e, quando ele responde, provisiona as chaves virtuais
# (gateway/keys.yaml). O healthcheck só fica verde depois disso, então quem depende do gateway
# com `condition: service_healthy` já encontra a chave da app criada. Roda a cada start, inclusive
# em `docker compose restart gateway`; o provisionamento é idempotente.
set -euo pipefail

READY=/tmp/gateway-ready
PY=/app/.venv/bin/python
rm -f "$READY"

litellm --config /etc/gateway/config.yaml --port 4000 &
pid=$!
trap 'kill -TERM "$pid" 2>/dev/null' TERM INT

for _ in $(seq 120); do
  if "$PY" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4000/health/readiness', timeout=2)" 2>/dev/null; then
    break
  fi
  kill -0 "$pid" 2>/dev/null || { echo "gateway: litellm encerrou durante a subida"; exit 1; }
  sleep 1
done

"$PY" /etc/gateway/provision_keys.py /etc/gateway/keys.yaml || { kill -TERM "$pid"; exit 1; }
touch "$READY"
echo "gateway: pronto"

wait "$pid"
