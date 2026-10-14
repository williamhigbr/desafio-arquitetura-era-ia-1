#!/usr/bin/env bash
# Passo da refatoração: reconstrói a app, roda a suíte de caracterização, mede e mostra /admin/calls.
# Uso: scripts/refactor_step.sh <nome-do-passo>
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
name="${1:?nome do passo}"
out="docs/evidence/refactor"
mkdir -p "$out"
{
  echo "## passo: $name"
  docker compose up -d --build --wait app >/dev/null 2>&1 || echo "falha na subida da app"
  until curl -sf -m 2 localhost:8000/health >/dev/null; do sleep 1; done
  echo "\$ pytest tests/characterization -q"
  .venv/bin/pytest tests/characterization -q -p no:cacheprovider 2>&1 | tail -3
  echo "\$ python metrics/coupling.py app/helpdesk --name $name"
  .venv/bin/python metrics/coupling.py app/helpdesk --name "$name" --out-dir "$out" | sed '$d'
  echo "\$ /admin/calls do último teste da suíte + uma F1/F4 de amostra"
  curl -s -X POST localhost:8090/admin/reset >/dev/null
  for r in classification extraction; do
    curl -s -o /dev/null -X POST "localhost:8000/tickets/$r" -H "Content-Type: application/json" \
      -d '{"ticket_id":"TK-00042","text":"Meu pedido #481516 não chegou e já passou do prazo, urgente"}'
  done
  curl -s "localhost:8090/admin/calls?last=5" | python3 -c \
    'import json,sys; [print("  ", c["provider"], c["model"], c["task"], c["status"]) for c in json.load(sys.stdin)]'
  echo "\$ grep -rn adapters app/helpdesk --include=*.py"
  grep -rn "adapters" app/helpdesk --include=*.py
} 2>&1 | tee "$out/$name.txt"
