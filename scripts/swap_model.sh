#!/usr/bin/env bash
# Troca de modelo por configuração: F1 (ticket-classify) de gpt-fake-large para gpt-fake-mini editando
# só gateway/config.yaml e reiniciando só o gateway; prova que a app não foi reiniciada; desfaz no fim.
# Grava docs/evidence/troca-de-modelo/f1-large-para-mini.txt.
# Uso (com o compose no ar):  scripts/swap_model.sh
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
CFG=gateway/config.yaml
OUT=docs/evidence/troca-de-modelo
mkdir -p "$OUT"
BACKUP=$(mktemp)
cp "$CFG" "$BACKUP"   # restaura a partir da cópia (não do git): funciona mesmo com o arquivo ainda não commitado
trap 'cp "$BACKUP" "$CFG"; rm -f "$BACKUP"; docker compose restart gateway >/dev/null 2>&1; docker compose up -d --wait gateway >/dev/null 2>&1' EXIT

T='{"ticket_id": "TK-00042", "text": "Meu pedido #481516 não chegou e já passou do prazo, urgente"}'
app_started() { docker inspect -f '{{.State.StartedAt}}' "$(docker compose ps -q app)"; }
f1() {
  curl -s -X POST localhost:8090/admin/reset >/dev/null
  curl -s -X POST localhost:8000/tickets/classification -H "Content-Type: application/json" -d "$T" \
    -w "  F1 pela borda: HTTP %{http_code} em %{time_total}s\n"
  curl -s "localhost:8090/admin/calls?last=1" | python3 -c \
    'import json,sys; c=json.load(sys.stdin)[0]; print("  /admin/calls:", c["provider"], c["model"], c["task"], c["status"])'
  echo "  app iniciada em: $(app_started)"
}

{
  echo "## Antes"
  f1
  echo; echo "## Troca: na entrada 'model_name: ticket-classify', *gpt-large -> *gpt-mini"
  python3 - "$CFG" <<'EOF'
import sys
p = sys.argv[1]; lines = open(p).read().split("\n")
i = lines.index("  - model_name: ticket-classify")
assert "*gpt-large" in lines[i + 1]
lines[i + 1] = lines[i + 1].replace("*gpt-large", "*gpt-mini")
open(p, "w").write("\n".join(lines))
EOF
  echo "\$ diff (antes -> depois) $CFG"; diff "$BACKUP" "$CFG"
  echo "\$ docker compose restart gateway"
  ( time { docker compose restart gateway >/dev/null 2>&1; docker compose up -d --wait gateway >/dev/null 2>&1; } ) 2>&1 | grep real
  echo; echo "## Depois (app não reconstruída nem reiniciada)"
  f1
  echo "\$ docker compose ps app gateway"; docker compose ps app gateway --format '  {{.Service}}: {{.Status}}'
} 2>&1 | tee "$OUT/f1-large-para-mini.txt"
echo; echo "(desfazendo: restaura a cópia de $CFG e reinicia o gateway)"
