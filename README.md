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

Testes de caracterização (contrato HTTP pela borda, com o compose no ar):

```
python3 -m venv .venv && . .venv/bin/activate
pip install -r tests/requirements.txt && pytest tests/characterization -q
```

A suíte (`tests/characterization/`) chama só `localhost:8000`, e usa `POST localhost:8090/admin/reset` antes de cada teste para deixar o provider em modo normal. Os valores esperados são golden masters capturados da app recebida: corpo completo de F1 e F4 (dois tickets, um com nota fiscal e pedido no mesmo texto), texto exato da sugestão da F2 e relatório completo de 1 dia da F3 (`tests/characterization/golden/`), além dos `422` de entrada inválida. A suíte não fixa nem a quantidade de chamadas ao provider nem qual provider atende, porque isso muda legitimamente na refatoração. Para outra base, use `HELPDESK_URL` e `PROVIDER_URL`. Leva ~30 s, quase todo na F2 (~16 s) e na F3 (~10 s).

Prova de que a suíte pega mudança de comportamento: trocar temporariamente `EXTRACTION_MODEL` e `SUGGESTION_MODEL` para `mini` e reconstruir a app derruba exatamente 2 testes (F4 com nota fiscal e F2); revertido, os 19 voltam a passar.

## Métrica

_A preencher (Etapas 4, 6 e 8)._

## Tabela de capacidades

_A preencher (Etapas 5 e 7)._

## Tabela de fallback

_A preencher (Etapa 7)._

## Fluxos

_A preencher (Etapa 8)._

## Troca de modelo

_A preencher (Etapa 7)._

## Roteiro de governança

_A preencher (Etapa 7)._

## Mapa de decisões

_A preencher (Etapa 9)._
