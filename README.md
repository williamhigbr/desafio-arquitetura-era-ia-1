# Helpdesk com IA: da integração frágil à arquitetura resiliente

Entrega do desafio [Da integração frágil à arquitetura resiliente](https://github.com/devfullcycle/desafio-arquitetura-era-ia-1). O enunciado completo continua no repositório base.

## Visão geral

_A preencher ao final (Etapa 9)._

## Diagnóstico da v1

Medido sobre a aplicação recebida (`app/` idêntico ao repositório base), sempre pela borda (`localhost:8000`), com o `provider-fake` como instrumento de prova. As saídas completas estão em [`docs/evidence/diagnostico/`](docs/evidence/diagnostico/) e são reproduzíveis com `scripts/diagnose.sh [d1|d2|d3-500|d3-timeout|d4|all]`. As características de cada modelo (`large` × `mini`) e os padrões dos SDKs estão em [`docs/evidence/etapa1-reconhecimento.md`](docs/evidence/etapa1-reconhecimento.md).

Os dois SDKs instalados (`openai` 3.19.0 e `anthropic` 1.8.0) usam por padrão timeout de leitura de 600 s e 2 retries, ou seja, 3 tentativas. A borda (`edge/nginx.conf`) encerra com `504` quando a app passa 30 s sem enviar nenhum byte. O timeout que vale para o usuário é o da borda, não o da aplicação.

### D1. O relatório mensal de temas nunca chega

```
curl -s -o /dev/null -w "HTTP %{http_code} total=%{time_total}s\n" -X POST localhost:8000/reports/topics \
  -H "Content-Type: application/json" -d '{"start": "2026-08-01", "end": "2026-08-31"}'
curl -s "localhost:8090/admin/calls?last=50"    # repetido a cada minuto
```

Observado ([`d1-relatorio.txt`](docs/evidence/diagnostico/d1-relatorio.txt)):

- `HTTP 504` da borda em 30,0 s, e o cliente não recebe nada.
- Depois do 504, a app continua trabalhando: as chamadas `topics` em `/admin/calls` seguem crescendo minuto a minuto (4 → 10 → … → 34) até completar os 34 lotes (5.000 tickets ÷ 150), cerca de 5,5 min depois do pedido.
- Foram consumidos 222.114 tokens de entrada e 12.162 de saída no `claude-fake-large`, ≈ US$ 0,85 pela tabela de preços do provider, gastos num relatório que ninguém vai receber. Cada nova tentativa do usuário repete o custo inteiro.

Causa:

- `app/helpdesk/report.py`, linhas 15–18: os lotes de 150 tickets são processados um depois do outro, dentro da requisição HTTP (~9,8 s por lote × 34 lotes).
- `app/helpdesk/main.py`, linhas 30–32: a rota é síncrona (`def`), roda no threadpool e não fica sabendo que o cliente desistiu. Desistir não é cancelar.
- `app/helpdesk/config.py`, linha 18: `TICKETS_PER_CALL = 150`. O mês tem ~222 mil tokens, contra 8.000 de contexto por modelo, então o particionamento é inevitável; o problema é executá-lo de forma síncrona.

### D2. A sugestão de resposta deixa o atendente olhando uma tela parada

```
curl -sN -o /dev/null -w "HTTP %{http_code} ttfb=%{time_starttransfer}s total=%{time_total}s\n" \
  -X POST localhost:8000/tickets/reply-suggestion -H "Content-Type: application/json" \
  -d '{"ticket_id": "TK-00042", "text": "Meu pedido #481516 não chegou e já passou do prazo, urgente"}'
```

Observado ([`d2-sugestao.txt`](docs/evidence/diagnostico/d2-sugestao.txt)): `HTTP 200`, `ttfb=15,98 s`, `total=15,98 s`, `content-type: application/json`. O primeiro byte chega junto com o último. `/admin/calls` mostra uma chamada `anthropic / claude-fake-large / suggest` com `stream=false`, 606 tokens de saída e 15.951 ms. Pelo README do provider, o primeiro token sai em 0,8 s; os outros ~15 s são geração que o atendente poderia estar lendo.

Causa:

- `app/helpdesk/llm.py`, linhas 20–26: `messages.create(...)` sem `stream=True`, então a resposta só existe quando o modelo termina.
- `app/helpdesk/suggestion.py`, linhas 5–7, e `main.py`, linhas 20–22: a rota devolve um JSON único.

Com ~610 tokens a 40 tokens/s, a sugestão fica a ~14 s dos 30 s da borda. Qualquer degradação do provider (modo `slow`) leva a F2 ao `504`.

### D3. Com um provider fora do ar ou travado, parte do helpdesk para

Provider fora do ar:

```
curl -s -X POST localhost:8090/admin/failures -H "Content-Type: application/json" \
  -d '{"provider": "anthropic", "mode": "error_500"}'
# depois, as 4 features pela borda
```

Observado ([`d3-provider-500.txt`](docs/evidence/diagnostico/d3-provider-500.txt)):

| Feature | Provider fixo | Resultado |
|---|---|---|
| F1 classificação | openai | `200` em 1,1 s |
| F4 extração | openai | `200` em 1,1 s |
| F2 sugestão | anthropic | `500 Internal Server Error` em 1,3 s |
| F3 relatório (1 dia) | anthropic | `500 Internal Server Error` em 1,2 s |

`/admin/calls` mostra 3 chamadas `claude-fake-large` com `status=500` por requisição de F2 e de F3: os 2 retries padrão do SDK, todos no mesmo provider caído. Nenhuma chamada foi para a OpenAI, que estava saudável e tem um modelo equivalente.

Provider travado:

```
curl -s -X POST localhost:8090/admin/failures -H "Content-Type: application/json" \
  -d '{"provider": "anthropic", "mode": "timeout"}'
# F2 pela borda; F1 em seguida
```

Observado ([`d3-provider-timeout.txt`](docs/evidence/diagnostico/d3-provider-timeout.txt)):

- F2 termina em `HTTP 504` da borda, em 30,0 s. Nesse meio-tempo, F1 (openai) responde `200` em 1,1 s.
- `/admin/calls` mostra a primeira chamada `suggest` às 22:08:51, com `duration_ms=null` (presa). Ela termina com `504` do provider em 300.004 ms, e o SDK abre a segunda tentativa às 22:13:51, cinco minutos depois de o cliente ter recebido o 504.
- Pelo padrão de 3 tentativas × 300 s, a app fica ocupada por até ~15 min por requisição abandonada.

Causa:

- `app/helpdesk/llm.py`, linhas 8–9: clientes criados sem `timeout` nem `max_retries`, então valem os 600 s / 2 retries do SDK, 20 vezes os 30 s da borda.
- `app/helpdesk/llm.py`, linhas 12–26, e `config.py`, linhas 9–13: cada feature está amarrada a um único provider e modelo físico, sem fallback.
- O retry existe, mas repete no mesmo destino que está falhando.

### D4. Trocar o modelo de uma feature exige mudar código e reconstruir a aplicação

```
# 1) troca temporária em código (desfeita em seguida)
sed -i '' 's/^CLASSIFICATION_MODEL = "gpt-fake-large"/CLASSIFICATION_MODEL = "gpt-fake-mini"/' app/helpdesk/config.py
docker compose restart app                       # só reiniciar
# F1 pela borda; /admin/calls?last=1
docker compose up -d --build --wait app          # reconstruir a imagem
# F1 pela borda; /admin/calls?last=1
git checkout -- app/ && docker compose up -d --build --wait app
```

Observado ([`d4-troca-modelo.txt`](docs/evidence/diagnostico/d4-troca-modelo.txt)):

| Passo | Modelo em `/admin/calls` | Tempo da F1 |
|---|---|---|
| `config.py` alterado + `docker compose restart app` | `gpt-fake-large` (a troca não teve efeito) | 1,13 s |
| `docker compose up -d --build --wait app` (rebuild de ~12 s) | `gpt-fake-mini` | 0,41 s |
| `git checkout -- app/` + rebuild | `gpt-fake-large` | 1,11 s |

Uma troca de modelo é uma mudança de código: commit, build de imagem e novo deploy da aplicação inteira, com indisponibilidade da app durante o recreate. Ao final, `git status app/` fica limpo.

Causa:

- `app/helpdesk/config.py`, linhas 10–13: nomes físicos dos modelos como constantes Python.
- `app/Dockerfile`, linha 6: `COPY helpdesk ./helpdesk`, então o código (e a configuração junto) fica congelado na imagem.
- `config.py`, linhas 3–7: o mesmo módulo carrega as chaves dos providers e as base URLs. A app conhece credencial, endereço, formato e modelo de cada provider; o `compose.yaml` entrega o `.env` inteiro ao serviço `app` (`env_file: .env`).

## Como rodar

Pré-requisitos: Docker com Compose v2 e Python 3.12.

Subida:

```
cp .env.example .env
docker compose up -d --build --wait
curl -s localhost:8000/health        # {"status":"ok"}
```

Serviços: `edge` (borda, `:8000`), `app`, `gateway` (LiteLLM, `:4000`), `db` (Postgres do gateway) e `provider-fake` (`:8090`). A chave virtual da app (`GATEWAY_APP_KEY`) é criada pelo próprio gateway a cada subida (`gateway/entrypoint.sh` + `gateway/keys.yaml`), sem passo manual. Quem já tinha um `.env` de antes do gateway precisa refazer o `cp .env.example .env`.

Testes de caracterização (contrato HTTP pela borda, com o compose no ar):

```
python3 -m venv .venv && . .venv/bin/activate
pip install -r tests/requirements.txt && pytest tests/characterization -q
```

A suíte (`tests/characterization/`) chama só `localhost:8000`, e usa `POST localhost:8090/admin/reset` antes de cada teste para deixar o provider em modo normal. Os valores esperados são golden masters capturados da app recebida: corpo completo de F1 e F4 (dois tickets, um com nota fiscal e pedido no mesmo texto), texto exato da sugestão da F2 e relatório completo de 1 dia da F3 (`tests/characterization/golden/`), além dos `422` de entrada inválida. A suíte não fixa nem a quantidade de chamadas ao provider nem qual provider atende, porque isso muda legitimamente na refatoração. Para outra base, use `HELPDESK_URL` e `PROVIDER_URL`. Leva ~30 s, quase todo na F2 (~16 s) e na F3 (~10 s).

Prova de que a suíte pega mudança de comportamento (feita na v1): trocar temporariamente `EXTRACTION_MODEL` e `SUGGESTION_MODEL` de `app/helpdesk/config.py` para `mini` e reconstruir a app derruba exatamente 2 testes (F4 com nota fiscal e F2); revertido, os 19 voltam a passar. A partir da v2 o equivalente é trocar o modelo das capacidades em `gateway/config.yaml`.

Métrica de acoplamento (não precisa do compose no ar):

```
pip install -r metrics/requirements.txt
python metrics/coupling.py app/helpdesk --name <nome>        # grava metrics/results/<nome>.csv e .png
pytest metrics/tests -q                                       # testes do próprio script

# medir uma tag com o script atual:
git worktree add ../v1 v1-coupled
python metrics/coupling.py ../v1/app/helpdesk --name v1-coupled
git worktree remove ../v1
```

## Métrica

Régua do requisito 3, implementada em `metrics/coupling.py` com `ast`: componente = cada `.py` de `app/helpdesk/` (exceto `__init__.py`); dependência = import relativo ou absoluto que resolve para outro componente; A = classes que herdam de `Protocol`/`ABC` ÷ classes; I = Ce/(Ce+Ca); D = |A+I−1|.

### v1-coupled

[`metrics/results/v1-coupled.csv`](metrics/results/v1-coupled.csv) · ![A × I v1-coupled](metrics/results/v1-coupled.png)

| component | ca | ce | i | a | d |
|---|---|---|---|---|---|
| classification | 1 | 3 | 0.75 | 0 | 0.25 |
| config | 6 | 0 | 0 | 0 | 1 |
| extraction | 1 | 3 | 0.75 | 0 | 0.25 |
| llm | 4 | 1 | 0.2 | 0 | 0.8 |
| main | 0 | 5 | 1 | 0 | 0 |
| report | 1 | 4 | 0.8 | 0 | 0.2 |
| schemas | 5 | 0 | 0 | 0 | 1 |
| suggestion | 1 | 3 | 0.75 | 0 | 0.25 |
| tickets | 1 | 1 | 0.5 | 0 | 0.5 |

Não há nenhuma abstração (A = 0 em todos). Três componentes caem na zona de dor (I < 0,5 e A < 0,5): `config` (D = 1), `schemas` (D = 1) e `llm` (D = 0,8). `llm` é o problema real: concentra SDK, provider, formato e credencial, que são as partes voláteis, e as 4 features dependem dele. `schemas` são tipos de valor do contrato HTTP.

### v2-decoupled

[`metrics/results/v2-decoupled.csv`](metrics/results/v2-decoupled.csv) · ![A × I v2-decoupled](metrics/results/v2-decoupled.png)

| component | ca | ce | i | a | d |
|---|---|---|---|---|---|
| adapters.gateway | 1 | 1 | 0.5 | 0 | 0.5 |
| adapters.tickets_file | 1 | 1 | 0.5 | 0 | 0.5 |
| classification | 1 | 2 | 0.67 | 0 | 0.33 |
| extraction | 1 | 2 | 0.67 | 0 | 0.33 |
| main | 0 | 7 | 1 | 0 | 0 |
| ports | 6 | 0 | 0 | 1 | 0 |
| report | 1 | 2 | 0.67 | 0 | 0.33 |
| schemas | 5 | 0 | 0 | 0 | 1 |
| suggestion | 1 | 2 | 0.67 | 0 | 0.33 |

O que saiu de onde, e por quê:

- `llm` (D 0,8) deixou de existir. O papel dele foi dividido entre `ports.LLMGateway`, que é a abstração da qual as features dependem e fica sobre a reta (A = 1, I = 0), e `adapters.gateway`, o único componente da app que chama o gateway.
- `config` (D 1) também deixou de existir, absorvido por `main`, que é o ponto de composição e o único lugar que lê o ambiente.
- `tickets` virou `TicketSource`, em `ports`, mais `adapters.tickets_file`.
- As features e os adapters são folhas concretas com um único cliente (`main`), por isso ficam em I 0,5–0,67 e D 0,33–0,5, fora da zona de dor. O plano registra por que não se criaram dependências para empurrá-los para a reta.

Única exceção que sobra: `schemas`, os tipos de valor do contrato HTTP. Eles mudam só quando o contrato muda, e o contrato é fixado pela suíte. ADR da métrica: _Etapa 9_.

## Tabela de capacidades

Mapeamento em [`gateway/config.yaml`](gateway/config.yaml). A app conhece só os 4 nomes lógicos e os chama pelo componente `app/helpdesk/adapters/gateway.py`, o único da app que fala com o gateway (SDK `openai`, `max_retries=0`, timeout de 25 s). Retry, timeout e fallback moram no gateway.

| Capacidade lógica | Feature | Modelo primário | Fallback técnico | Modelo fraco | Timeout por tentativa | Máx. tentativas por destino |
|---|---|---|---|---|---|---|
| `ticket-classify` | F1 | `openai/gpt-fake-large` | `anthropic/claude-fake-large` | `openai/gpt-fake-mini` | 5 s | 2 em 5xx, 3 em 429, 1 em timeout, em cada destino |
| `ticket-extract` | F4 | `openai/gpt-fake-large` | `anthropic/claude-fake-large` | nenhum (falha explícita) | 5 s | 2 em 5xx, 3 em 429, 1 em timeout, em cada destino |
| `ticket-reply` | F2 | `anthropic/claude-fake-large` | `openai/gpt-fake-large` | `anthropic/claude-fake-mini` | 20 s (4 s sem bytes no streaming, Etapa 8) | 2 em 5xx, 3 em 429, 1 em timeout, em cada destino |
| `topics-report` | F3 (por lote) | `anthropic/claude-fake-large` | `openai/gpt-fake-large` | `anthropic/claude-fake-mini` | 20 s | 2 em 5xx, 3 em 429, 1 em timeout, em cada destino |

Política de retry (`router_settings.retry_policy`, igual para todos os destinos):

- `500`/`503` (`InternalServerErrorRetries`/`ServiceUnavailableErrorRetries: 1`): 1 retry, com backoff exponencial do LiteLLM (0,5 s × 2ⁿ + jitter de até 0,75 s).
- `429` (`RateLimitErrorRetries: 2`): 2 retries, respeitando o `Retry-After` do provider (2 s + jitter).
- Timeout (`TimeoutErrorRetries: 0`): nenhum retry, vai direto ao próximo destino. Repetir gastaria o orçamento de latência inteiro de novo.
- 4xx de requisição e o resto (`DefaultRetries: 0`): nenhum retry.
- `disable_cooldowns: true`: cada requisição percorre a cadeia inteira, então as contagens acima valem sempre, e não só para a primeira requisição depois de uma falha.

Contagens observadas em `/admin/calls`, pela borda, com `python3 scripts/resilience.py` (saídas em [`docs/evidence/resiliencia/`](docs/evidence/resiliencia/)):

| Cenário (provider-fake) | Feature | Resultado pela borda | Tentativas por destino |
|---|---|---|---|
| normal | F1 / F4 / F2 / F3 | `200` em 1,2 / 1,2 / 16,2 / 9,9 s | 1 no primário |
| `anthropic` em `error_500` | F2 | `200` em 18,2 s, texto do large | claude-large 2 → gpt-large 1 |
| `anthropic` em `error_500` | F3 (1 dia) | `200` em 12,2 s, relatório idêntico | claude-large 2 → gpt-large 1 |
| `openai` em `error_500` | F1 / F4 | `200` em 3,4 / 3,3 s, mesma resposta | gpt-large 2 → claude-large 1 |
| os dois `large` em `error_500` | F1 | `200` em 5,5 s (mini) | gpt-large 2 → claude-large 2 → gpt-mini 1 |
| os dois `large` em `error_500` | F2 | `200` em 7,9 s, sugestão de 1.096 caracteres (mini) | claude-large 2 → gpt-large 2 → claude-mini 1 |
| os dois `large` em `error_500` | F3 (1 dia) | `200` em 7,9 s (mini; mesmo relatório) | claude-large 2 → gpt-large 2 → claude-mini 1 |
| os dois `large` em `error_500` | F4 | `503` em 4,5 s | gpt-large 2 → claude-large 2 |
| `gpt-fake-large` em `timeout` | F1 | `200` em 6,2 s (≤ 15 s) | gpt-large 1 (504, cortado em 5 s) → claude-large 1 |
| `openai` em `error_429` | F1 | `200` em 7,8 s | gpt-large 3 (intervalo ~2,3 s, Retry-After) → claude-large 1 |
| os dois providers em `error_500` | F1 / F2 / F3 | `503` em 8,0 / 6,9 / 7,7 s | large 2 → large 2 → mini 2 |
| os dois providers em `error_500` | F4 | `503` em 4,8 s | gpt-large 2 → claude-large 2 |

Limitação conhecida até a Etapa 8: a F2 ainda é síncrona. Com o primário da F2 em `timeout`, o gateway espera 20 s e o fallback leva mais ~16 s, o que passa do timeout de 25 s da app. A app responde `503` em 25 s (nunca `504` da borda), mas o fallback termina no gateway depois disso. O streaming da Etapa 8 resolve, porque com `stream_timeout: 4` a troca de destino acontece em 4 s.

## Tabela de fallback

O que acontece quando não há nenhum modelo `large` disponível (os dois `large` em `error_500`; cenário `large-500`):

| Feature | Comportamento | Status | O que o cliente recebe |
|---|---|---|---|
| F1 classificação | Responde com `gpt-fake-mini` | `200` | Mesmo contrato e mesma classificação (no `mini` ela é idêntica à do `large`) |
| F2 sugestão | Responde com `claude-fake-mini` (degradação declarada) | `200` | Sugestão mais curta (~45% do tamanho), com os mesmos pontos; o atendente revisa antes de enviar |
| F3 relatório | Cada lote usa `claude-fake-mini` | `200` | Mesmo relatório (no `mini` os temas e as contagens são idênticos) |
| F4 extração | Falha explícita, sem modelo fraco | `503` + `Retry-After: 30` | `{"detail": "Serviço de IA indisponível no momento. Tente novamente mais tarde.", "capability": "ticket-extract"}` |

Por que a F4 não tem `mini`: nos testes da Etapa 1, o `mini` devolve o número da nota fiscal no lugar do pedido e perde o produto (5 de 8 entradas), num formato válido que a validação da app não pega. A F4 alimenta uma automação sem revisão humana, então um pedido errado vira uma troca errada. Evidência: [`docs/evidence/etapa1-reconhecimento.md`](docs/evidence/etapa1-reconhecimento.md).

Sem nenhum modelo disponível (os dois providers fora), as quatro features respondem `503` com o mesmo corpo, em menos de 8,1 s.

## Fluxos

_A preencher (Etapa 8)._

## Troca de modelo

Arquivo: [`gateway/config.yaml`](gateway/config.yaml). Altere o `litellm_params` da entrada da capacidade. Por exemplo, para a F1 passar a usar o `mini`, troque a âncora `*gpt-large` por `*gpt-mini` na entrada `model_name: ticket-classify`. Para um modelo sem âncora, escreva `{model: openai/<modelo>, input_cost_per_token: ..., output_cost_per_token: ..., <<: *openai, timeout: 5}`. Depois:

```
docker compose restart gateway
```

A app não é reconstruída nem reiniciada, e os modelos vêm só do arquivo (`store_model_in_db: false`). `scripts/swap_model.sh` faz a troca da F1 de `large` para `mini`, confere e desfaz no fim ([`docs/evidence/troca-de-modelo/f1-large-para-mini.txt`](docs/evidence/troca-de-modelo/f1-large-para-mini.txt)):

| Momento | `/admin/calls` | F1 pela borda | App iniciada em |
|---|---|---|---|
| Antes | `openai gpt-fake-large classify 200` | `200` em 1,35 s | 20:27:17 |
| Depois do `restart gateway` (~18 s) | `openai gpt-fake-mini classify 200` | `200` em 0,66 s | 20:27:17 (inalterado; `docker compose ps`: app `Up 32 minutes`, gateway `Up 12 seconds`) |

As chaves virtuais sobrevivem ao restart: o entrypoint do gateway as reaplica (`provision_keys: chave ... atualizada`).

## Roteiro de governança

Chaves em [`gateway/keys.yaml`](gateway/keys.yaml), criadas pelo gateway a cada subida: `governance-budget` (`max_budget` US$ 0,0002), `governance-rpm` (`rpm_limit` 2) e a da app. Os preços dos modelos estão em `gateway/config.yaml`; sem eles o custo seria zero e o orçamento nunca estouraria. O roteiro completo é `scripts/governance.sh`, que reinicia o gateway, zera o gasto das duas chaves e conta `/admin/calls` antes e depois de cada chamada ([`docs/evidence/governanca/roteiro.txt`](docs/evidence/governanca/roteiro.txt)). À mão:

```
set -a; . ./.env; set +a
calls() { curl -s "localhost:8090/admin/calls?last=5000" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))'; }
ask() { curl -s -w "\nHTTP %{http_code}\n" localhost:4000/v1/chat/completions -H "Authorization: Bearer $1" \
  -H "Content-Type: application/json" \
  -d '{"model": "ticket-classify", "messages": [{"role": "user", "content": "TASK: classify\nMeu pedido não chegou"}]}'; }

# orçamento: cada classificação custa ~US$ 0,00015; espere uns segundos entre as chamadas (gasto é assíncrono)
calls; ask $GATEWAY_BUDGET_KEY; sleep 3; ask $GATEWAY_BUDGET_KEY; sleep 3; calls
ask $GATEWAY_BUDGET_KEY; calls      # 429 budget_exceeded, e o contador não muda

# limite de requisições: 2 por minuto
calls; ask $GATEWAY_RPM_KEY; ask $GATEWAY_RPM_KEY; ask $GATEWAY_RPM_KEY; calls   # 3ª: 429, e só 2 chamadas a mais
```

Observado:

| Chave | Chamada | Gateway | `/admin/calls` |
|---|---|---|---|
| `governance-budget` | 1ª, 2ª | `200` | 0 → 1 → 2 |
| `governance-budget` | 3ª, 4ª | `429 budget_exceeded` ("Current cost: 0.000295, Max budget: 0.0002") | 2 → 2 (nenhuma chamada ao provider) |
| `governance-rpm` | 1ª, 2ª | `200` | 2 → 3 → 4 |
| `governance-rpm` | 3ª, 4ª | `429 throttling_error` ("Current limit: 2, Remaining: 0") | 4 → 4 (nenhuma chamada ao provider) |
| `helpdesk-app` | controle | `200` | 4 → 5 |

O orçamento estoura na 2ª chamada (US$ 0,000295 > 0,0002), e a recusa vem na 3ª, porque o gasto é conferido antes da chamada e contabilizado depois dela. Assim, uma chave nunca ultrapassa o teto em mais do que o custo de uma chamada. A janela de rpm é de um minuto; para repetir o roteiro, rode `scripts/governance.sh`, que reinicia o gateway e zera os contadores.

## Mapa de decisões

_A preencher (Etapa 9)._
