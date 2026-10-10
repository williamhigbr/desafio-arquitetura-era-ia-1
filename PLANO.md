# PLANO — Da integração frágil à arquitetura resiliente

Plano de execução do desafio, em etapas curtas. Cada etapa tem: objetivo, tarefas, o que delegar ao agente de IA, critério de pronto (ligado aos Critérios de Aceite) e perguntas de fixação com resposta. Tente responder antes de abrir a resposta.

Regra de trabalho com agentes, válida para todas as etapas: o agente produz e você aceita ou rejeita com base em algo determinístico (teste, métrica, `/admin/calls`, `curl` pela borda). Nunca aceite um "funcionou" do agente sem a evidência rodada por você.

---

## 0. Mapa geral

| Marco | Estado | O que precisa ser verdade |
|---|---|---|
| tag `v1-coupled` | `app/` idêntico ao base + `tests/characterization/` | Suíte passa contra a app recebida |
| tag `v2-decoupled` | App refatorada, chamando só o gateway (nomes lógicos + chave do gateway) | Mesma suíte, sem alteração, passa; mesmos modos de resposta |
| `main` | Gateway com governança/resiliência/fallback; F1 sync, F2 streaming, F3 assíncrono, F4 sync | Todos os critérios de aceite |

Arquitetura-alvo (proposta, a confirmar no piloto):

```
edge (nginx, 30s) ──► app (FastAPI)
                        main.py            ← ponto de composição (único que importa o adapter)
                        ports.py           ← Protocols (LLMGateway, ...)  A alto, I baixo
                        adapters/gateway.py← único componente que usa SDK/HTTP do gateway
                        classification.py, suggestion.py, extraction.py, report.py
                                           ← dependem de ports/schemas, recebem o gateway injetado
                        schemas.py         ← tipos de valor (candidato a "estável por natureza")
                     ──► gateway (LiteLLM) ──► provider-fake (openai | anthropic)
                           └─ db (Postgres)   ← exigido por virtual keys/budget → precisa de ADR
```

Fatos do terreno já levantados no código (confirme medindo, é isso que vai para o diagnóstico):

| Fato | Onde | Consequência |
|---|---|---|
| Chaves e nomes físicos de modelo em código | `app/helpdesk/config.py` | Troca de modelo = rebuild (o `Dockerfile` faz `COPY helpdesk`) |
| Dois clientes SDK, sem `timeout`/`max_retries` | `app/helpdesk/llm.py` | Valem os padrões dos SDKs |
| F3 em lotes de 150, sequencial e síncrono | `app/helpdesk/report.py` | 5.000 tickets → 34 chamadas em série |
| F2 síncrona, `large` gera ~2.400 caracteres | `suggestion.py` + tabela do provider | ~600 tokens ÷ 40 tok/s + 0,8 s ≈ 16 s de tela parada |
| Borda: `proxy_read_timeout 30s`, buffering padrão do nginx ligado | `edge/nginx.conf` | 504 após 30 s sem byte; streaming pode chegar em bloco |
| `app` recebe `.env` inteiro via `env_file` | `compose.yaml` | Chaves dos providers no ambiente da app |

---

## Etapa 1 — Reconhecimento

Objetivo: entender o terreno antes de opinar.

Tarefas
- `cp .env.example .env && docker compose up -d --build --wait`
- Ler `provider-fake/README.md`, `edge/nginx.conf`, todo `app/helpdesk/`.
- Chamar as 4 tarefas direto no provider (`localhost:8090`) em `large` e `mini`, nos dois providers. Guardar as saídas em um arquivo de anotações (vão virar evidência do ADR de fallback).
- Descobrir os padrões dos SDKs na versão instalada:
  ```
  docker compose exec app python -c "import openai, anthropic; print(openai.DEFAULT_TIMEOUT, openai.DEFAULT_MAX_RETRIES); print(anthropic.DEFAULT_TIMEOUT, anthropic.DEFAULT_MAX_RETRIES)"
  ```

Agente: peça um script `scripts/compare_models.sh` que chame cada tarefa nos 4 modelos com o mesmo texto e grave as saídas. Você lê e compara.

Pronto quando: você sabe dizer, por tarefa, se `mini` difere de `large` e como.

<details><summary>P1. Em quais tarefas o <code>mini</code> é pior, e isso importa para quem?</summary>

Confirme com as chamadas (a evidência do ADR tem que ser a saída observada):
- `classify`: mesma saída nos dois tamanhos → fallback com `mini` é aceitável para F1.
- `topics`: mesma saída → aceitável para F3.
- `suggest`: `mini` gera texto bem mais curto (~1.000 vs ~2.400 caracteres). Atendente humano revisa e edita → resposta pior é melhor que nenhuma; aceitável para F2 (degradação declarada).
- `extract`: `mini` pega o primeiro número de 6 dígitos do texto (pode ser nota fiscal, não o pedido) e não identifica produto. F4 alimenta automação sem revisão humana → um campo errado gera troca errada. Falhar explicitamente é melhor. Teste com um texto que tenha NF e pedido, ex.: `"NF 123456, pedido #654321, fone de ouvido bluetooth com defeito"`.
</details>

<details><summary>P2. Quais os padrões de timeout e retry dos SDKs e por que isso é um problema com a borda?</summary>

Nos SDKs Python, historicamente: timeout de 600 s e 2 retries (3 tentativas) para `openai` e `anthropic` (confirme com o comando acima na versão instalada). A borda desiste em 30 s sem bytes. Com o provider em `timeout` (segura 300 s e devolve 504), a app espera até 300 s por tentativa e repete: a borda já respondeu 504 ao cliente, mas a app segue ocupando thread e gerando chamadas por até ~15 min. O timeout que vale para o usuário é o menor da cadeia (30 s), e não é o seu.
</details>

---

## Etapa 2 — Diagnóstico da v1 (seção do README)

Objetivo: transformar as 4 dores em fatos com comando, resultado e causa no código. Não alterar `app/` (exceto a demonstração temporária da dor 4, desfeita antes da tag).

| Dor | Como reproduzir | O que registrar | Causa |
|---|---|---|---|
| D1 Relatório não chega | `time curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:8000/reports/topics -H "Content-Type: application/json" -d '{"start":"2026-08-01","end":"2026-08-31"}'`; depois `curl -s "localhost:8090/admin/calls?last=50"` a cada minuto | 504 em ~30 s; chamadas `topics` continuam crescendo após o 504 (tokens pagos para ninguém) | `report.py`: loop sequencial síncrono; `main.py`: rota síncrona |
| D2 Sugestão trava a tela | `curl -s -w "\n%{time_starttransfer}s %{time_total}s\n"` na F2 | Primeiro byte ≈ tempo total ≈ 16 s | `suggestion.py` + `llm.call_anthropic` sem stream |
| D3 Provider cai, helpdesk para | `POST /admin/failures {"provider":"anthropic","mode":"error_500"}` → F2/F3 falham, F1/F4 ok; repetir com `timeout` | Status 500 e 3 chamadas por requisição em `/admin/calls` (retries do SDK); com `timeout`, 504 da borda | `llm.py`: sem fallback, sem timeout, retries padrão; um provider fixo por feature em `config.py` |
| D4 Trocar modelo exige deploy | Alterar temporariamente `REPORT_MODEL` em `config.py`; `docker compose restart app` não muda nada; só `docker compose up -d --build app` muda; reverter com `git checkout app/` | `/admin/calls` mostra o modelo antigo até o rebuild | `config.py` (modelo no código) + `Dockerfile` (`COPY helpdesk`) |

Agente: peça um `scripts/diagnose.sh` que execute os cenários e salve saídas em texto. Você interpreta e escreve o README.

Pronto quando: as 4 dores têm comando, resultado (status, tempo, trechos de `/admin/calls`) e arquivo/linha da causa. `git status app/` limpo.

<details><summary>P3. Por que o 504 da borda não interrompe o trabalho da app?</summary>

O nginx fecha a conexão com o cliente e com o upstream, mas a rota é `def` síncrona rodando no threadpool do Starlette: nada sinaliza cancelamento à função, que segue o loop até o fim. "Desistir não é cancelar." A solução não é cancelar melhor, é não prender a requisição: F3 vira assíncrona.
</details>

---

## Etapa 3 — Testes de caracterização → tag `v1-coupled`

Objetivo: fixar o comportamento atual pela API HTTP (Feathers).

Tarefas
- `tests/characterization/` com `pytest` + `httpx`, `tests/requirements.txt` com versões fixas.
- Fixture que faz `POST localhost:8090/admin/reset` antes de cada teste.
- Por feature: ao menos 1 caso feliz com corpo completo fixado (valores, não só chaves) e 1 erro de entrada (`422`: campo ausente, `text` vazio; F3 com `start > end`).
- F2: fixe o texto exato da sugestão (saída é determinística) ou prefixo + tamanho + hash.
- F3: use um período curto (1 dia) para caber abaixo de 30 s ainda na v1.
- Não fixe detalhes que mudam legitimamente na v2 (ex.: quantidade de chamadas em `/admin/calls`). Fixe o contrato HTTP.
- Comando único: `pip install -r tests/requirements.txt && pytest tests/characterization -q`.
- Commit, `git tag v1-coupled`, `git push origin main --tags`.

Agente: "Escreva testes de caracterização para estas rotas. Primeiro rode cada chamada, capture a resposta real e use-a como valor esperado (golden master). Não invente valores." Rejeite qualquer teste que só verifique status.

Pronto quando: suíte verde contra a app recebida; `git diff <commit-base> v1-coupled -- app/` vazio.

<details><summary>P4. Qual a diferença entre teste de caracterização e teste de especificação?</summary>

Especificação diz o que o sistema deveria fazer; caracterização registra o que ele faz hoje, inclusive comportamentos estranhos. Na refatoração você quer o segundo: qualquer diferença indica mudança de comportamento, não de estrutura.
</details>

<details><summary>P5. Por que a suíte não pode mudar entre <code>v1-coupled</code> e <code>v2-decoupled</code>?</summary>

Porque ela é o juiz da refatoração. Se o juiz muda junto com o réu, não há prova de que o comportamento foi preservado. Por isso o modelo por trás de cada capacidade na v2 continua `large`: um `mini` mudaria saídas fixadas.
</details>

---

## Etapa 4 — Script de métrica e medição da v1

Objetivo: régua fixa A × I com Main Sequence.

Tarefas
- `metrics/coupling.py <caminho/app/helpdesk> --name <nome>` usando `ast`:
  - componente = cada `.py` (exceto `__init__.py`), nome com pontos relativo a `helpdesk/`;
  - resolver `import x`, `from . import a, b`, `from .a import X`, `from ..ports import X`, `from helpdesk.a import X`; contar só os que resolvem para outro componente (conjunto, sem duplicatas);
  - A = classes que herdam de `Protocol`/`typing.Protocol`/`ABC`/`abc.ABC` ÷ total de classes;
  - I = Ce/(Ce+Ca) ou 0; D = |A+I−1|; `round(x, 2)`.
- Saída `metrics/results/<nome>.csv` (cabeçalho exato `component,ca,ce,i,a,d`) e `<nome>.png` (matplotlib, reta A+I=1, rótulos por ponto, zonas de dor/inutilidade sombreadas).
- `git worktree add ../v1 v1-coupled && python metrics/coupling.py ../v1/app/helpdesk --name v1-coupled`.
- Testes unitários do script com um pacote de exemplo (fixture) cobrindo import relativo de dois níveis e `from . import a`.

Agente: entregue a régua do requisito 3 literalmente e peça os testes antes do script. Valide contra a conta manual abaixo.

Pronto quando: o CSV gerado bate com a tabela de autoverificação.

<details><summary>P6. Autoverificação: calcule à mão a v1 antes de rodar o script</summary>

| component | ca | ce | i | a | d | Zona de dor? |
|---|---|---|---|---|---|---|
| classification | 1 | 3 | 0.75 | 0 | 0.25 | não |
| config | 6 | 0 | 0 | 0 | 1 | sim |
| extraction | 1 | 3 | 0.75 | 0 | 0.25 | não |
| llm | 4 | 1 | 0.2 | 0 | 0.8 | sim |
| main | 0 | 5 | 1 | 0 | 0 | não |
| report | 1 | 4 | 0.8 | 0 | 0.2 | não |
| schemas | 5 | 0 | 0 | 0 | 1 | sim |
| suggestion | 1 | 3 | 0.75 | 0 | 0.25 | não |
| tickets | 1 | 1 | 0.5 | 0 | 0.5 | não (I não é < 0,5) |

`schemas` tem 7 classes pydantic, nenhuma abstrata → A = 0. Se o script divergir disso, o erro está no script.
</details>

<details><summary>P7. Por que <code>llm</code> na zona de dor é um problema real e <code>schemas</code> talvez não?</summary>

Zona de dor = muito depended-on (estável) e concreto: difícil de mudar e com motivo para mudar. `llm` concentra provider, SDK e formato, coisas voláteis, e 4 features dependem dele: toda troca de provider propaga. `schemas` são tipos de valor do contrato, que mudam raramente; estar na zona é efeito da fórmula, não um risco. Isso justifica declarar `schemas` como estável por natureza no ADR da métrica. `llm`/adapter do gateway e features nunca podem ser exceção.
</details>

---

## Etapa 5 — Plano, gateway e piloto

Objetivo: escrever o plano, subir o gateway e refatorar um componente só, medindo antes e depois.

Tarefas
- `docs/refactoring-plan.md` (versão 1): componentes-alvo (`llm`, `config`, features), estratégia por componente, critério de pronto. Sugestão de critério: suíte verde + nenhum componente na zona de dor fora das exceções + só `main` importa o adapter + `grep` de nomes físicos/chaves vazio em `app/`.
- Gateway (LiteLLM, imagem fixada, ex.: `ghcr.io/berriai/litellm:v1.102.1`, a testada pelo provider):
  - `gateway/config.yaml` com capacidades lógicas, ex.: `ticket-classify`, `ticket-reply`, `ticket-extract`, `topics-report`, mapeadas para os mesmos `large` da v1;
  - `env_file: .env` só no `gateway`; `app` recebe apenas `GATEWAY_URL` e `GATEWAY_API_KEY` via `environment`;
  - Postgres para virtual keys (serviço `db` → ADR);
  - criação automática da virtual key da app com valor fixo (o `/key/generate` aceita `key`), idempotente.
- Piloto: um componente (sugestão: F1 `classification` + extrair `ports.py` + `adapters/gateway.py`). Medir antes/depois e registrar no plano.
- Revisão do plano com base no que o piloto mostrou (ex.: "`config` continuou na zona de dor; decidimos X porque Y").

Agente: entregue a régua, a tabela da v1, o critério de pronto e a regra "um componente por vez; rode `pytest tests/characterization` e `metrics/coupling.py` após cada mudança e cole a saída". Registre no plano o prompt usado e o que você rejeitou.

Pronto quando: F1 passa pelo gateway, suíte verde, medição do piloto registrada, revisão escrita.

<details><summary>P8. Como as virtual keys serão criadas sem passo manual? Quais as opções?</summary>

(a) Entrypoint customizado no próprio `gateway`: sobe o LiteLLM, espera o health, chama `/key/generate` com chaves fixas e ignora "já existe". Sem serviço extra. (b) Serviço one-shot `gateway-init` (curl) com `depends_on: condition: service_completed_successfully` → mais uma peça e mais um ADR, e convém testar o comportamento com `docker compose up --wait`. Prefira (a) pela entrega enxuta; seja qual for, precisa sobreviver a `docker compose restart gateway` (exigido na troca de modelo).
</details>

<details><summary>P9. O que fazer com <code>config</code>, que tende a ficar com Ce = 0 e A = 0?</summary>

Pergunte o que ele contém depois da refatoração: URL do gateway, chave do gateway, nomes lógicos, caminho dos tickets. Duas saídas legítimas: (1) leitura de configuração é responsabilidade do ponto de composição, então os valores são lidos em `main.py` e injetados nos componentes (o módulo some porque o papel dele foi absorvido, não por causa da fórmula); (2) manter e declarar estável por natureza, se você argumentar que nomes lógicos são estáveis por design. Não crie import artificial para mover o ponto. Essa escolha é um bom candidato para a "revisão do plano".
</details>

<details><summary>P10. Por que inverter a dependência (features → <code>ports</code> ← adapter) reduz o acoplamento?</summary>

Antes, as features dependiam de algo concreto e volátil (`llm`). Depois, dependem de uma abstração estável (`Protocol`, A = 1), e o concreto (adapter) depende dela também. Trocar o adapter não toca nas features; só `main.py` sabe qual implementação usar. No gráfico: `ports` vai para A alto/I baixo (perto da reta), o adapter fica instável e concreto (I alto), ambos com D baixo.
</details>

---

## Etapa 6 — Escalar a refatoração → tag `v2-decoupled`

Tarefas
- Repetir o padrão do piloto em F2, F3, F4 e `tickets` (se fizer sentido, `TicketSource` em `ports`).
- Um único cliente (SDK `openai` apontado para o gateway, que é OpenAI-compatible), com `max_retries=0` e `timeout` explícito na app; remover `anthropic` do `requirements.txt` se não for mais usado.
- Manter o comportamento: F2 ainda síncrona, F3 ainda síncrona nesta tag.
- Verificar: `pytest tests/characterization -q` sem alterar a suíte; `grep -rE "gpt-fake|claude-fake" app/`; `grep -rE "FAKE_OPENAI_KEY|FAKE_ANTHROPIC_KEY|sk-fake-openai|sk-ant-fake" app/`; `docker compose exec app env`; `docker compose config` (serviço `app`).
- Medir `v2-decoupled`, versionar CSV/PNG, atualizar o plano, `git tag v2-decoupled && git push --tags`.
- Conferir `git diff v1-coupled v2-decoupled -- tests/characterization/` vazio.

<details><summary>P11. Por que <code>max_retries=0</code> na app?</summary>

Retries em vários lugares se multiplicam: 3 tentativas no SDK × 3 no gateway = até 9 chamadas por requisição, além de estourar o orçamento de latência. O dono do retry passa a ser o gateway (lugar único, com backoff e fallback). O `/admin/calls` mostra a multiplicação imediatamente se você esquecer.
</details>

---

## Etapa 7 — Governança e resiliência no gateway

Tarefas
- Preços dos modelos simulados na config (`input_cost_per_token`/`output_cost_per_token`, ex.: `gpt-fake-large` = 2.5e-6 / 1e-5); sem isso o custo é zero e o budget nunca estoura.
- Chaves: uma com `max_budget` baixo e uma com `rpm_limit` baixo, além da chave da app. Roteiro no README chamando o gateway direto (exponha a porta, ex.: `4000`) e contando `/admin/calls` antes/depois.
- Resiliência por capacidade: `timeout` explícito, `num_retries` limitado com backoff, `fallbacks` para o `large` do outro provider; fallback para `mini` só em F1, F2, F3 (decisão da Etapa 1). F4 sem `mini`.
- Testar cada modo e registrar o número real de tentativas por destino observado em `/admin/calls` (é o que vai na Tabela de capacidades):
  - primário em `error_500` → tentativas no primário + sucesso no outro provider;
  - os dois `large` em `error_500` → F1/F2/F3 respondem com `mini`; F4 responde erro explícito (ex.: `503` com mensagem);
  - primário da F1 em `timeout` → resposta em ≤ 15 s;
  - `error_429` → retry respeitando backoff.
- Troca de modelo: editar `gateway/config.yaml`, `docker compose restart gateway`, conferir `/admin/calls` e que o `app` não reiniciou (`docker compose ps`, coluna de uptime). Garanta que a config do arquivo prevalece sobre o banco (ex.: não armazenar modelos no DB).

Agente: ótimo para descobrir as chaves certas da config do LiteLLM na versão fixada; exija que cada afirmação venha com o trecho da documentação e um teste rodado contra o `/admin/calls`.

<details><summary>P12. Como dimensionar o timeout da F1 para caber em 15 s com o primário em <code>timeout</code>?</summary>

F1 normal leva ~1,1 s (0,8 s até o primeiro token + saída curta). Um timeout de ~4–5 s por tentativa dá folga. Retry em timeout gasta o orçamento inteiro de novo, então para timeout prefira não repetir no mesmo destino e ir direto ao fallback: 5 s (primário) + ~1,1 s (fallback) ≈ 6 s < 15 s. Faça a conta para o pior caso declarado e registre no ADR.
</details>

<details><summary>P13. Por que a recusa por budget/rate limit precisa acontecer antes do provider?</summary>

Porque governança que deixa a chamada passar e só registra o gasto não governa nada: o token já foi pago. O teste é objetivo: o número de chamadas em `/admin/calls` não pode aumentar com a requisição recusada. Atenção: o LiteLLM pode contabilizar gasto de forma assíncrona; no roteiro, espere alguns segundos entre a chamada que consome o budget e a que deve ser recusada.
</details>

---

## Etapa 8 — Modos de execução (`main`)

Decisão por feature (árvore: alguém está esperando agora? cabe no orçamento de latência? é consumível aos poucos?):

| Feature | Esperando? | Cabe na latência? | Consumível aos poucos? | Modo |
|---|---|---|---|---|
| F1 | Sim (cliente) | Sim (~1 s ≤ 3 s) | Irrelevante (saída curta, fixa) | Síncrono com timeout |
| F2 | Sim (atendente) | Não inteiro (~16 s), sim o 1º trecho (0,8 s) | Sim (texto lido/editado ao chegar) | Streaming SSE |
| F3 | Não (painel, sem callback) | Não (34 lotes, minutos) | Não (relatório só faz sentido completo) | Async Request-Reply (polling) |
| F4 | Não humano, mas automação chamadora espera | Sim (saída curta) | Não (JSON) | Síncrono com timeout |

Tarefas
- F2: `StreamingResponse` com `media_type="text/event-stream"`, eventos `data: {"chunk": ...}`, `event: end`, e `event: error` ao capturar exceção no meio do stream. Header `X-Accel-Buffering: no` para o nginx da borda não acumular (você não pode mudar `edge/`). Validar com `curl -N` pela borda e com `midstream_error`.
- F3: `POST` → `202` + `Location: /reports/topics/status/{id}` + `Retry-After`; `GET status` → `200 {"state","progress"}` ou `failed` com `reason`; `done` → `303` para `/reports/topics/{id}`. Execução em background dentro da app (tarefa asyncio/thread) com store em memória. Falha de um lote após fallbacks → `failed` imediato; prazo máximo por tarefa para nunca ficar presa em `running`. Com os dois providers em `error_500`, `failed` em ≤ 60 s.
- Opcional: paralelizar lotes com concorrência limitada para reduzir o tempo total (cuidado com rate limit da chave da app).
- Testes da `main` para os contratos novos (pode atualizar `tests/characterization/` na `main` ou criar `tests/contract/`). Um script de aceite que percorre os critérios ajuda na Etapa 10.
- Medir `main`, versionar CSV/PNG; conferir zona de dor e que só `main.py` importa o adapter.

<details><summary>P14. Streaming na F2 resolve "não dar 504"? E por que não usar streaming na F3?</summary>

Na F2 sim, por consequência: bytes chegam a cada fração de segundo, então a borda nunca fica 30 s sem dados. Mas o motivo da escolha é outro: o atendente consome o texto aos poucos. Na F3 streaming só esconderia o problema (conexão aberta por minutos, painel sem ninguém olhando, resultado útil só no fim). Se o ADR justificar F3 com "evita o 504", ele está justificando por acidente; a justificativa é "ninguém está esperando e o trabalho não cabe no orçamento de latência".
</details>

<details><summary>P15. Qual o custo do assíncrono em processo, e quando ele deixaria de ser suficiente?</summary>

Estado em memória: perde-se em restart (fora de escopo) e quebra com múltiplos workers do uvicorn (o status pode cair num processo que não tem o job), então mantenha 1 worker e declare no ADR. Passaria a exigir fila/worker/banco quando houver múltiplas réplicas, necessidade de sobreviver a restart ou volume que justifique escalar o processamento separadamente. Cada serviço desses exige ADR ligado a requisito; hoje nenhum requisito os pede.
</details>

<details><summary>P16. Se o streaming chegar "em bloco" no <code>curl -N</code>, onde procurar?</summary>

Peça por peça: (1) o provider envia SSE? (`curl -N` direto em `:8090` com `stream: true`); (2) o gateway repassa em stream? (`curl -N` em `:4000`); (3) a app faz `yield` por chunk, sem acumular, sem middleware de compressão? (`curl -N` em `app:8080` de dentro da rede); (4) a borda: buffering padrão do nginx → `X-Accel-Buffering: no`.
</details>

---

## Etapa 9 — ADRs e README

ADRs mínimos sugeridos (`docs/adr/`, linha `Nível:` em cada):

| # | Decisão | Nível |
|---|---|---|
| 0001 | Política de providers aceitos e teto de gasto mensal com IA | corporativa |
| 0002 | Adotar AI Gateway self-hosted (LiteLLM) entre app e providers | solução |
| 0003 | Capacidades lógicas e mapeamento para modelos físicos | solução |
| 0004 | Modo de execução por feature (uma seção por feature) | solução |
| 0005 | Política de fallback técnico e com modelo fraco (uma seção por feature, com evidência do `mini`) | solução |
| 0006 | Governança: chaves, orçamentos e limites | solução ou corporativa (justifique) |
| 0007 | Postgres para o gateway (justifica o serviço extra) | software |
| 0008 | Leitura da métrica e exceções (estáveis por natureza) | software |
| 0009 | Ports & adapters na app / único componente que chama o gateway | software |

README com todas as seções do Entregável: Visão geral, Diagnóstico da v1, Como rodar, Métrica, Tabela de capacidades, Tabela de fallback, Fluxos (com `curl`), Troca de modelo, Roteiro de governança, Mapa de decisões. Identifique explicitamente o componente que chama o gateway.

Regra: cada afirmação de ADR tem uma seção "Evidência" com um comando que a prova. Se não dá para provar pela borda ou pelo `/admin/calls`, o ADR ou o código está errado.

<details><summary>P17. Como decidir o nível de um ADR?</summary>

Pergunte quem precisa participar e quanto tempo a decisão deve durar. Corporativa: qual problema resolver e com que limites para a empresa (providers aceitos, teto de gasto), envolve negócio/compliance, dura anos. Solução: como resolver o problema (gateway, modos de execução, fallback), envolve arquitetura e produto, dura enquanto a solução existir. Software: como construir (ports & adapters, banco do gateway, leitura da métrica), envolve o time, muda com mais facilidade.
</details>

---

## Etapa 10 — Verificação final do zero

- Clone limpo do fork → `cp .env.example .env && docker compose up -d --build --wait && curl -s localhost:8000/health`.
- Percorrer cada critério de aceite seguindo só o README (idealmente com um sub-agente "auditor" que recebe apenas o README e os critérios, sem o seu contexto).
- Conferir: `git diff <base> -- provider-fake edge data` vazio; tags publicadas; `metrics` da `main` sobre `v1-coupled` reproduz o CSV versionado.

---

## Armadilhas frequentes

- Fixar na suíte algo que muda na v2 (ex.: contagem de chamadas) → suíte quebra na v2 e você é tentado a mudá-la.
- Esquecer `max_retries=0` na app → tentativas multiplicadas no `/admin/calls`.
- Budget que nunca estoura por falta de preços na config.
- Chave do gateway criada à mão → avaliador não consegue subir sem passo manual.
- `env_file: .env` ainda no serviço `app` → chaves dos providers no ambiente.
- Desenhar código para a fórmula (import artificial) em vez de explicar a estrutura.
- Job da F3 preso em `running` porque a exceção do background não atualiza o estado.
