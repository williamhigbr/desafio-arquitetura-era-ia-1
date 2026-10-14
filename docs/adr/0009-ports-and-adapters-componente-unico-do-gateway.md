# 0009. Ports & adapters na app: `adapters/gateway.py` é o único componente que chama o gateway

- Status: aceita
- Data: 2026-10-13
- Nível: software

## Contexto

Mesmo com o gateway (ADR 0002), a app ainda precisa de um cliente HTTP ou SDK para falar com ele. Se cada feature criasse o próprio cliente, o acoplamento a transporte, credencial, timeout e retry voltaria espalhado pelas features, e a troca de transporte (por exemplo, para streaming) tocaria em todas elas.

## Opções consideradas

1. Cada feature usa o SDK `openai` diretamente.
2. Um módulo utilitário concreto importado pelas features: é o `llm.py` da v1 com outro nome, com Ca alto, concreto e na zona de dor.
3. Inversão de dependência: as features dependem de portas (`Protocol`) e recebem a implementação injetada. Os adapters implementam as portas, e só o ponto de composição conhece os adapters.

## Decisão

Opção 3.

- `ports.py` define `LLMGateway` (`complete`, `stream`), `TicketSource` e `JobStore`.
- `adapters/gateway.py` (`GatewayClient`) é o único componente da app que importa o SDK `openai`. Ele aplica `max_retries=0` e timeout de 25 s, e traduz falhas em `GatewayUnavailable` (que vira `503`) e `StreamInterrupted` (que vira `event: error`).
- `adapters/tickets_file.py` lê o JSONL, e `adapters/memory_jobs.py` guarda o estado da F3.
- `main.py` lê o ambiente, instancia os adapters e os passa às features.
- Cada feature conhece só a porta, os schemas e o próprio nome de capacidade.

Os adapters herdam explicitamente da porta (`class GatewayClient(LLMGateway)`). A dependência é real, e herdar a deixa visível na métrica e para quem lê o código.

## Consequências

- Trocar transporte, autenticação ou fonte de tickets é trocar um adapter em `main.py`, sem tocar nas features.
- As features podem ser exercitadas com implementações falsas das portas, sem gateway.
- A composição é manual, sem framework de injeção: são três objetos, e `main.py` continua legível.

## Evidência

```
grep -rn "import" app/helpdesk --include=*.py | grep -E "openai|adapters"
# app/helpdesk/adapters/gateway.py:  import openai / from openai import OpenAI
# app/helpdesk/main.py:              from .adapters.gateway ... / .adapters.memory_jobs ... / .adapters.tickets_file ...
grep -n "max_retries" app/helpdesk/adapters/gateway.py      # max_retries=0
```

Em `metrics/results/main.csv`, `adapters.gateway` tem `ca = 1` (só `main`) e `ce = 1` (só `ports`), e `ports` tem `a = 1`.
