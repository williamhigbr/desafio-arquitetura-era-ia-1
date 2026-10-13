#!/usr/bin/env bash
# Reproduz as quatro dores da v1 e grava as saídas em docs/evidence/diagnostico/<cenario>.txt.
#
# Uso (com o compose no ar):  scripts/diagnose.sh [d1|d2|d3-500|d3-timeout|d4|all]
#
# Cada cenário começa com `docker compose restart app` + /admin/reset para não herdar
# trabalho pendente do anterior (a v1 continua processando depois do 504, ver D1).
# O D4 altera app/helpdesk/config.py temporariamente e sempre o restaura (trap).
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/docs/evidence/diagnostico"
EDGE="http://localhost:8000"
FAKE="http://localhost:8090"
TICKET='{"ticket_id": "TK-00042", "text": "Meu pedido #481516 não chegou e já passou do prazo, urgente"}'
MONTH='{"start": "2026-08-01", "end": "2026-08-31"}'
mkdir -p "$OUT"
cd "$ROOT" || exit 1

log() { echo "$*"; }
run() { echo "\$ $*"; eval "$@"; echo; }   # mostra o comando e a saída
wait_edge() { for _ in $(seq 60); do curl -sf -m 2 "$EDGE/health" >/dev/null && return; sleep 1; done; echo "borda não respondeu"; }
fresh() { docker compose restart app >/dev/null 2>&1; wait_edge; curl -s -X POST "$FAKE/admin/reset" >/dev/null; }
CALLS_PY='
import json, sys
calls = json.load(sys.stdin)
print(f"{len(calls)} chamada(s) em /admin/calls (mais recente primeiro)")
for c in calls:
    print("  {} {:9} {:17} {:8} stream={} status={} in={} out={} ms={}".format(
        c["ts"][11:19], c["provider"], c["model"], c["task"], c["stream"], c["status"],
        c["input_tokens"], c["output_tokens"], c["duration_ms"]))
'
calls() { curl -s "$FAKE/admin/calls?last=${1:-50}" | python3 -c "$CALLS_PY"; }
TOPICS_PY='
import json, sys
t = [x for x in json.load(sys.stdin) if x["task"] == "topics"]
done = [x for x in t if x["duration_ms"] is not None]
print("{} chamadas topics recebidas ({} concluídas), {} tokens de entrada, {} de saída".format(
    len(t), len(done), sum(x["input_tokens"] for x in t), sum(x["output_tokens"] or 0 for x in t)))
'
topics_count() { curl -s "$FAKE/admin/calls?last=200" | python3 -c "$TOPICS_PY"; }
post() {  # post <rota> <json>: status e tempos medidos pela borda
  curl -s -o /tmp/diag_body -w "HTTP %{http_code}  ttfb=%{time_starttransfer}s  total=%{time_total}s\n" \
    -X POST "$EDGE$1" -H "Content-Type: application/json" -d "$2"
  echo "corpo: $(head -c 300 /tmp/diag_body)"
}

d1() {  # Relatório do mês não chega, e a app continua gastando tokens depois do 504
  fresh
  log "## D1 — relatório do mês (2026-08-01 a 2026-08-31) pela borda"; date -u +"início %H:%M:%S UTC"
  run post /reports/topics "'$MONTH'"
  log "## /admin/calls logo após o 504"; topics_count
  for i in 1 2 3 4 5 6; do
    sleep 60; log "## +${i} min após o 504: $(topics_count)"
  done
  log "## /admin/calls final (últimas 3)"; calls 3
}

d2() {  # Sugestão de resposta: o atendente não vê nada até o fim
  fresh
  log "## D2 — sugestão de resposta pela borda (curl -N, mede primeiro byte e total)"
  run "curl -sN -o /dev/null -w 'HTTP %{http_code}  ttfb=%{time_starttransfer}s  total=%{time_total}s  bytes=%{size_download}  content-type=%{content_type}\n' -X POST $EDGE/tickets/reply-suggestion -H 'Content-Type: application/json' -d '$TICKET'"
  calls 5
}

d3_500() {  # Provider fora do ar: metade do helpdesk para junto
  fresh
  log "## D3a — anthropic inteiro em error_500"
  run "curl -s -X POST $FAKE/admin/failures -H 'Content-Type: application/json' -d '{\"provider\": \"anthropic\", \"mode\": \"error_500\"}'"
  for r in /tickets/classification /tickets/extraction /tickets/reply-suggestion; do log "### $r"; post "$r" "$TICKET"; done
  log "### /reports/topics (1 dia)"; post /reports/topics '{"start": "2026-08-01", "end": "2026-08-01"}'
  calls 20
  curl -s -X POST "$FAKE/admin/reset" >/dev/null
}

d3_timeout() {  # Provider travado: a borda corta em 30 s e a app segue presa
  fresh
  log "## D3b — anthropic inteiro em timeout (segura 300 s sem bytes)"
  run "curl -s -X POST $FAKE/admin/failures -H 'Content-Type: application/json' -d '{\"provider\": \"anthropic\", \"mode\": \"timeout\"}'"
  log "### /tickets/reply-suggestion"; post /tickets/reply-suggestion "$TICKET"
  log "### /tickets/classification durante o travamento (openai normal)"; post /tickets/classification "$TICKET"
  calls 5
  log "## +5,5 min: o SDK recebe o 504 do provider após 300 s e repete (retry padrão)"
  sleep 330; calls 5
  curl -s -X POST "$FAKE/admin/reset" >/dev/null
  docker compose restart app >/dev/null 2>&1   # encerra as tentativas restantes
}

d4() {  # Trocar o modelo exige mudar código e reconstruir a imagem
  fresh
  local cfg="app/helpdesk/config.py"
  trap 'git checkout -- app/helpdesk/config.py' RETURN
  log "## D4 — trocar o modelo da F1 para gpt-fake-mini"
  run "grep -n CLASSIFICATION_MODEL $cfg"
  sed -i.bak 's/^CLASSIFICATION_MODEL = "gpt-fake-large"/CLASSIFICATION_MODEL = "gpt-fake-mini"/' "$cfg" && rm -f "$cfg.bak"
  run "git diff --stat -- app/"
  log "### 1) só reiniciar a app (docker compose restart app)"
  docker compose restart app >/dev/null 2>&1; wait_edge
  curl -s -X POST "$FAKE/admin/reset" >/dev/null; post /tickets/classification "$TICKET"; calls 1
  log "### 2) reconstruir a imagem (docker compose up -d --build --wait app)"
  run "( time docker compose up -d --build --wait app >/dev/null 2>&1 ) 2>&1 | grep real"
  wait_edge; curl -s -X POST "$FAKE/admin/reset" >/dev/null; post /tickets/classification "$TICKET"; calls 1
  log "### 3) desfazer: git checkout -- app/ e reconstruir"
  git checkout -- app/helpdesk/config.py
  docker compose up -d --build --wait app >/dev/null 2>&1; wait_edge
  run "git status --short -- app/ && echo '(app/ limpo)'"
  curl -s -X POST "$FAKE/admin/reset" >/dev/null; post /tickets/classification "$TICKET"; calls 1
}

case "${1:-all}" in
  d1) d1 2>&1 | tee "$OUT/d1-relatorio.txt" ;;
  d2) d2 2>&1 | tee "$OUT/d2-sugestao.txt" ;;
  d3-500) d3_500 2>&1 | tee "$OUT/d3-provider-500.txt" ;;
  d3-timeout) d3_timeout 2>&1 | tee "$OUT/d3-provider-timeout.txt" ;;
  d4) d4 2>&1 | tee "$OUT/d4-troca-modelo.txt" ;;
  all) for s in d2 d3-500 d4 d3-timeout d1; do "$0" "$s"; done ;;
  *) echo "uso: $0 [d1|d2|d3-500|d3-timeout|d4|all]"; exit 2 ;;
esac
