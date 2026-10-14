# Etapa 1 — Reconhecimento: evidências

Coletado em 2026-10-13 contra o `provider-fake` local, com `app/` idêntico ao repositório base.
Dados brutos: [`model-comparison.json`](model-comparison.json) (100 chamadas, gerado por `python3 scripts/compare_models.py`).

## 1. Padrões dos SDKs (versões instaladas na imagem da app)

```
$ docker compose exec -T app python -c "import openai, anthropic; ..."
openai 3.19.0     Timeout(connect=5.0, read=600, write=600, pool=600) 2
anthropic 1.8.0   Timeout(connect=5.0, read=600, write=600, pool=600) 2
```

Os dois SDKs esperam até 600 s por leitura e fazem 2 retries, ou seja, 3 tentativas. `app/helpdesk/llm.py` (linhas 8–9) cria os clientes sem `timeout` nem `max_retries`, então esses padrões valem. A borda desiste em 30 s sem byte (`edge/nginx.conf`, `proxy_read_timeout 30s`). Com o provider em `timeout` (segura 300 s), uma requisição pode ocupar a app por até 3 × 300 s = 15 min depois de o cliente já ter recebido 504.

## 2. As 4 features pela borda (provider normal)

| Feature | Rota | Resultado | Tempo (ttfb = total) | `/admin/calls` |
|---|---|---|---|---|
| F1 | `POST /tickets/classification` | `{"category":"delivery","priority":"high"}` | 1,10 s | openai / gpt-fake-large / classify, 19→11 tokens |
| F4 | `POST /tickets/extraction` | `{"order_number":"#481516","product":null}` | 1,08 s | openai / gpt-fake-large / extract, 19→11 tokens |
| F2 | `POST /tickets/reply-suggestion` | sugestão de ~2.400 caracteres | 15,99 s (primeiro byte = fim) | anthropic / claude-fake-large / suggest, 19→606 tokens |
| F3 | `POST /reports/topics` (1 dia, 2026-08-01) | `total_tickets: 137` | 9,82 s | anthropic / claude-fake-large / topics, 6.132→360 tokens |

Entrada F1/F2/F4: `{"ticket_id":"TK-00042","text":"Meu pedido #481516 não chegou e já passou do prazo, urgente"}`.

Projeção da F3 para o mês: 5.000 tickets em 34 lotes de 150, ~9,8 s por lote em série, dá ≈ 5,5 min. Isso é muito acima dos 30 s da borda e aponta para a dor D1 (a confirmar na Etapa 2). O mês inteiro tem ~222 mil tokens de entrada, contra 8.000 de contexto por modelo, por isso o lote é inevitável.

Streaming direto no provider (`stream: true`, `suggest`, large): os headers chegam na hora, e os tokens começam após o TTFT declarado de 0,8 s. O total (~17,7 s) é o mesmo nos dois providers, o que confirma que streaming muda a espera percebida, não o tempo total.

## 3. `large` × `mini` por tarefa

Mesmas entradas nos 4 modelos: 6 tickets reais (`TK-00002`, `00015`, `00036`, `00004`, `00001`, `00007`) e 2 textos ad hoc. `topics` usa os 150 primeiros tickets, o mesmo lote da app.

| Tarefa | large openai = large anthropic | mini = large | Diferença observada | Latência large → mini |
|---|---|---|---|---|
| `classify` | 8/8 | 8/8 | nenhuma | 1,1 s → 0,4 s |
| `topics` | 1/1 | 1/1 | nenhuma (mesmos temas, contagens e exemplos) | 9,8 s → 2,7 s |
| `suggest` | 8/8 | 0/8 | mesmos pontos, em outra ordem, com ~45% do tamanho (~1.070 vs ~2.430 caracteres; ~270 vs ~610 tokens) | 16 s → 2,1 s |
| `extract` | 8/8 | 3/8 | `mini` pega o primeiro número de 6 dígitos (a nota fiscal) e nunca devolve `product` | 1,1 s → 0,4 s |

Os dois `mini` também são iguais entre si (8/8 em todas as tarefas). Logo, o fallback técnico entre providers é transparente nos dois tamanhos.

### Detalhe da `extract` (onde a qualidade importa)

| Entrada | large (os dois) | mini (os dois) | Erro do mini |
|---|---|---|---|
| TK-00002 "nota fiscal 530720 … pedido #605065" | `#605065`, null | `#530720`, null | pedido errado (é a NF) |
| TK-00015 "nota fiscal 197527 … pedido 972600" | `#972600`, null | `#197527`, null | pedido errado (é a NF) |
| TK-00036 "nota fiscal 927477 … tapete de yoga … pedido nº 389511" | `#389511`, `tapete de yoga` | `#927477`, null | pedido errado e produto perdido |
| TK-00007 "… notebook 14 polegadas …" (sem pedido) | null, `notebook 14 polegadas` | null, null | produto perdido |
| "NF 123456, pedido #654321, fone de ouvido bluetooth com defeito" | `#654321`, `fone de ouvido bluetooth` | `#123456`, null | pedido errado e produto perdido |
| TK-00004, TK-00001, AD-HOC-1 (só pedido, ou nada) | igual | igual | — |

Exposição no dataset: 717 dos 5.000 tickets (14,3%) citam "nota fiscal". Pela amostra, em todos eles o `mini` devolveria um número de pedido que existe no formato válido (`#NNNNNN`), mas está errado. A validação de formato em `extraction.py` (linha 15) não pega esse erro.

### Leitura preliminar para o ADR de fallback (decisão final na Etapa 7)

- F1 `classify`: `mini` idêntico ao `large`, então o fallback com modelo fraco não tem custo de qualidade observado.
- F3 `topics`: idêntico também. O fallback com `mini` é aceitável e ainda deixa o relatório ~3,6× mais rápido.
- F2 `suggest`: a resposta sai mais curta, mas tem o mesmo conteúdo. Um humano lê e edita antes de enviar, então uma resposta pior é melhor que nenhuma. Aceitável, como degradação declarada.
- F4 `extract`: o erro é silencioso e plausível (NF no lugar do pedido) e alimenta uma automação sem revisão humana. Falhar explicitamente é melhor do que responder com `mini`.

## 4. Fatos do terreno confirmados no código (`app/`)

| Fato | Onde |
|---|---|
| Chaves dos providers lidas do ambiente | `config.py` linhas 3–4 (`os.environ["FAKE_OPENAI_KEY"]`, `["FAKE_ANTHROPIC_KEY"]`) |
| Base URLs e modelos físicos fixos no código | `config.py` linhas 6–13 |
| F1/F4 em openai, F2/F3 em anthropic | `classification.py:11`, `extraction.py:12`, `suggestion.py:6`, `report.py:18` |
| Clientes sem timeout/retry explícitos | `llm.py` linhas 8–9 |
| Sem streaming em nenhuma chamada | `llm.py` linhas 12–26 |
| F3 em lotes de 150, em série, dentro da requisição | `report.py` linhas 15–18; `config.py:18` |
| Rotas `def` síncronas (threadpool; nada cancela o trabalho quando a borda desiste) | `main.py` linhas 15–32 |
| Código copiado na imagem, então trocar modelo exige rebuild | `app/Dockerfile` linha 6 (`COPY helpdesk ./helpdesk`) |
| `.env` inteiro injetado na app | `compose.yaml`, serviço `app`, `env_file: .env` |
| Borda: 30 s sem byte dá 504; buffering do nginx no padrão (ligado) | `edge/nginx.conf` |

## 5. Ambiente local (nota operacional, fora da entrega)

Esta máquina roda Podman 5.3 (machine) com Docker Compose v2 (`brew install docker-compose`, configurado em `~/.config/containers/containers.conf` → `compose_providers`). Duas diferenças do Podman em relação ao Docker são contornadas num `compose.override.yaml` local, ignorado via `.git/info/exclude` e não versionado:

- healthcheck em forma exec (`["CMD", ...]`) chega quebrado à API compat do Podman, então o override do serviço `app` usa `CMD-SHELL`;
- `edge/nginx.conf` usa o DNS embutido do Docker (`127.0.0.11`), que não existe no Podman, então um sidecar `edge-dns` (socat) encaminha esse endereço para o DNS da rede.

Nenhum dos dois toca `app/`, `edge/` ou `provider-fake/`. No Docker do avaliador o override não existe. Para conferir o comportamento "de fábrica", rode `docker compose -f compose.yaml up -d --build --wait` (lembrando que no Podman isso quebra pelos motivos acima).
