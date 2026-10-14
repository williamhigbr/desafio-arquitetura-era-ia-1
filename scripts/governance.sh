#!/usr/bin/env bash
# Roteiro de governança: recusa por orçamento e por limite de requisições, ANTES do provider.
# Chama o gateway direto (:4000) com as chaves de gateway/keys.yaml e conta /admin/calls antes/depois
# de cada chamada. Grava docs/evidence/governanca/roteiro.txt.
# Uso (com o compose no ar):  scripts/governance.sh
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
set -a; . ./.env; set +a
G=http://localhost:4000
F=http://localhost:8090
mkdir -p docs/evidence/governanca

count() { curl -s "$F/admin/calls?last=5000" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))'; }
ask() {  # ask <chave> <rótulo>
  local before after code
  before=$(count)
  code=$(curl -s -o /tmp/gov_body -w "%{http_code}" "$G/v1/chat/completions" \
    -H "Authorization: Bearer $1" -H "Content-Type: application/json" \
    -d '{"model": "ticket-classify", "messages": [{"role": "user", "content": "TASK: classify\nMeu pedido #481516 não chegou"}]}')
  sleep 0.5; after=$(count)
  echo "  $2: HTTP $code | /admin/calls $before -> $after ($((after - before)) chamada(s) ao provider)"
  [ "$code" = 200 ] || echo "    corpo: $(python3 -c 'import json,sys; e=json.load(open("/tmp/gov_body")).get("error",{}); print(e.get("type"), "|", str(e.get("message"))[:160])')"
}
spend() {
  curl -s "$G/key/info?key=$1" -H "Authorization: Bearer $LITELLM_MASTER_KEY" | python3 -c '
import json, sys
i = json.load(sys.stdin)["info"]
print("  /key/info {}: spend=US$ {:.6f} max_budget={} rpm_limit={}".format(
    i["key_alias"], i["spend"], i["max_budget"], i["rpm_limit"]))'
}

{
  echo "## Preparação: reinicia o gateway para zerar o gasto e a janela de rpm das chaves de governança"
  echo "\$ docker compose restart gateway   (o entrypoint recria/reaplica as chaves; o gasto é zerado com /key/update spend=0)"
  docker compose restart gateway >/dev/null 2>&1
  docker compose up -d --wait gateway >/dev/null 2>&1   # volta quando o healthcheck (chaves provisionadas) fica verde
  for k in "$GATEWAY_BUDGET_KEY" "$GATEWAY_RPM_KEY"; do
    curl -s -o /dev/null -X POST "$G/key/update" -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
      -H "Content-Type: application/json" -d "{\"key\": \"$k\", \"spend\": 0}"
  done
  curl -s -X POST "$F/admin/reset" >/dev/null

  echo; echo "## 1. Orçamento: chave governance-budget (max_budget US\$ 0,0002; 1 classificação ≈ US\$ 0,00016)"
  spend "$GATEWAY_BUDGET_KEY"
  # O gasto é contabilizado depois da resposta (assíncrono): espera entre chamadas. A recusa usa o
  # contador em memória do gateway ("Current cost" no corpo do 429); /key/info lê o banco, que o
  # LiteLLM atualiza em lote, então pode mostrar 0 por alguns segundos.
  ask "$GATEWAY_BUDGET_KEY" "1ª chamada"
  sleep 3
  ask "$GATEWAY_BUDGET_KEY" "2ª chamada"
  sleep 3
  ask "$GATEWAY_BUDGET_KEY" "3ª chamada (orçamento estourado)"
  ask "$GATEWAY_BUDGET_KEY" "4ª chamada (orçamento estourado)"

  echo; echo "## 2. Limite de requisições: chave governance-rpm (rpm_limit 2)"
  spend "$GATEWAY_RPM_KEY"
  ask "$GATEWAY_RPM_KEY" "1ª chamada"
  ask "$GATEWAY_RPM_KEY" "2ª chamada"
  ask "$GATEWAY_RPM_KEY" "3ª chamada (acima de 2/min)"
  ask "$GATEWAY_RPM_KEY" "4ª chamada (acima de 2/min)"

  echo; echo "## 3. Controle: a chave da app não é afetada"
  ask "$GATEWAY_APP_KEY" "chave helpdesk-app"
} 2>&1 | tee docs/evidence/governanca/roteiro.txt
rm -f /tmp/gov_body
