# Plano de refatoração: do helpdesk acoplado ao helpdesk atrás do gateway

Versão 1, de 2026-10-13: plano inicial, escrito antes do piloto. A seção "Revisões" registra o que mudou depois de medir.

## Objetivo e escopo

Levar a `v1-coupled` à `v2-decoupled`. Ao final, a app chama os modelos só pelo AI Gateway (LiteLLM), usando nomes lógicos de capacidade e uma chave do próprio gateway. O comportamento externo fica idêntico ao da v1: mesmas rotas, mesmos modos de resposta e a suíte de caracterização passando sem alteração. Os modos novos (streaming e assíncrono) e a resiliência ficam fora deste plano (Etapas 7 e 8).

## Ponto de partida (medido, `metrics/results/v1-coupled.csv`)

| component | ca | ce | i | a | d | leitura |
|---|---|---|---|---|---|---|
| classification | 1 | 3 | 0.75 | 0 | 0.25 | feature; depende de `llm` e `config` |
| config | 6 | 0 | 0 | 0 | 1 | zona de dor: mistura chaves, URLs e modelos físicos |
| extraction | 1 | 3 | 0.75 | 0 | 0.25 | feature |
| llm | 4 | 1 | 0.2 | 0 | 0.8 | zona de dor: SDKs, provider e formato, dos quais as 4 features dependem |
| main | 0 | 5 | 1 | 0 | 0 | rotas |
| report | 1 | 4 | 0.8 | 0 | 0.2 | feature, mais `tickets` |
| schemas | 5 | 0 | 0 | 0 | 1 | zona de dor pela fórmula; tipos de valor do contrato HTTP |
| suggestion | 1 | 3 | 0.75 | 0 | 0.25 | feature |
| tickets | 1 | 1 | 0.5 | 0 | 0.5 | leitura do arquivo de tickets |

## Componentes-alvo e estratégia

| Componente | Problema | Estratégia | Resultado esperado |
|---|---|---|---|
| `llm` | Concreto e muito dependido; concentra o que muda (provider, SDK, credencial, formato) | Inverter a dependência: extrair a abstração `ports.LLMGateway` (Protocol) e mover o concreto para `adapters/gateway.py`, que fala com o gateway pelo SDK `openai` (`max_retries=0`, timeout explícito). `llm.py` deixa de existir. | `ports`: A = 1, I ≈ 0, D ≈ 0. `adapters.gateway`: concreto, I alto, D baixo |
| features (`classification`, `extraction`, `suggestion`, `report`) | Dependem do concreto `llm` e de `config` (modelos físicos) | Compor em vez de importar: cada feature recebe o gateway injetado (parâmetro tipado com o port) e conhece só o nome lógico da capacidade que usa | Continuam instáveis (I alto) e concretas, perto da reta; Ce passa a ser `ports` + `schemas` |
| `main` | Rotas sem composição | Vira o ponto de composição e o único módulo que importa `adapters.gateway`; injeta o adapter nas features | I = 1, A = 0, D = 0 |
| `config` | Ca = 6 e concreto; contém credenciais dos providers e modelos físicos | Remover chaves, URLs dos providers e nomes físicos, deixando só o que a app precisa (URL e chave do gateway, timeout, arquivo de tickets, tamanho do lote) e reduzindo Ca | Hipótese: Ca cai, mas ele continua com Ce = 0 e na zona de dor. Decidir no piloto (ver P9 do PLANO) |
| `schemas` | Zona de dor pela fórmula | Não mexer. Candidato a "estável por natureza" no ADR da métrica: são tipos de valor do contrato HTTP, que a suíte fixa e que só mudam se o contrato mudar | Continua em D = 1, declarado como exceção |
| `tickets` | I = 0,5 | Etapa 6: avaliar um `TicketSource` em `ports`, se a leitura do arquivo precisar ser injetada em `report` | Avaliar depois do piloto |

Infraestrutura que acompanha a refatoração (v2):

- Serviço `gateway` (LiteLLM `v1.102.1`) com `gateway/config.yaml`, que mapeia as capacidades lógicas para os mesmos `large` da v1: `ticket-classify` e `ticket-extract` → `gpt-fake-large`; `ticket-reply` e `topics-report` → `claude-fake-large`.
- Serviço `db` (Postgres 16) para as virtual keys. A chave da app é criada automaticamente pelo entrypoint do gateway, de forma idempotente.
- `app` recebe só `GATEWAY_URL` e `GATEWAY_API_KEY`. O `env_file: .env` sai do serviço `app` quando a última feature tiver migrado.

## Critério de pronto (tag `v2-decoupled`)

Todos verificáveis por comando:

1. `pytest tests/characterization -q` verde, com `git diff v1-coupled -- tests/characterization/` vazio.
2. `python metrics/coupling.py app/helpdesk --name v2-decoupled`: nenhum componente na zona de dor (I < 0,5 e A < 0,5), fora as exceções declaradas no ADR da métrica. `adapters.gateway` e as features nunca podem ser exceção.
3. Só `main` importa `adapters.*`: `grep -rn "adapters" app/helpdesk --include=*.py` aponta só para `main.py`.
4. `grep -rE "gpt-fake|claude-fake" app/` e `grep -rE "FAKE_OPENAI_KEY|FAKE_ANTHROPIC_KEY|sk-fake-openai|sk-ant-fake" app/` vazios.
5. `docker compose exec app env` sem chaves de provider; `docker compose config` sem `env_file` no serviço `app`.
6. `/admin/calls` mostra as mesmas tarefas e modelos `large` da v1, uma tentativa por requisição no modo normal.

Ordem: piloto em F1 → avaliar e revisar este plano → F4 → F2 → F3 (com `tickets`) → `config` → remover `llm.py` e `anthropic` do `requirements.txt` → tag.

## Handoff para o agente de IA

O agente (Kiro, na CLI) recebe o prompt abaixo. Ele propõe e executa um componente por vez, sempre colando as saídas. Quem aceita ou rejeita é o autor, com base nas saídas, e nunca num "funcionou".

```
Contexto: app FastAPI em app/helpdesk, medida com metrics/coupling.py (régua do requisito 3:
componente = .py exceto __init__; dependência = import que resolve para outro componente;
A = Protocol/ABC ÷ classes; I = Ce/(Ce+Ca); D = |A+I−1|). Tabela da v1: metrics/results/v1-coupled.csv.

Tarefa: refatorar UM componente por vez para que as features dependam de ports.LLMGateway
(Protocol) e só main.py importe adapters/gateway.py, que chama o gateway LiteLLM com nomes
lógicos (ticket-classify, ticket-reply, ticket-extract, topics-report) via SDK openai com
max_retries=0 e timeout explícito.

Regras:
- Não altere tests/characterization/, provider-fake/, edge/, data/.
- Não crie import ou classe só para mover um ponto no gráfico; toda dependência precisa ter
  um motivo estrutural que você consiga explicar.
- Mantenha um modelo large por trás de cada capacidade (mini muda saídas fixadas).
- Após cada mudança, rode e cole a saída de:
    pytest tests/characterization -q
    python metrics/coupling.py app/helpdesk --name <passo>
    curl -s "localhost:8090/admin/calls?last=5"
- Pare depois de cada componente e espere aceite.

Critério de pronto: o da seção "Critério de pronto" de docs/refactoring-plan.md.
```

Regras de aceite usadas pelo autor:

- Rejeito qualquer passo com a suíte vermelha, ou que tenha alterado a suíte.
- Rejeito se a métrica piorar num componente sem que haja uma explicação estrutural.
- Rejeito se `/admin/calls` mostrar modelo diferente do `large` esperado ou mais de uma chamada por requisição.
- Rejeito import "decorativo" (dependência sem uso real), mesmo que melhore o número.

O registro do que foi aceito e rejeitado fica em "Diário do handoff", abaixo.

## Piloto: F1 (`classification`)

Escolhida por ser a feature mais curta (~1 s, saída fixa) e por exigir as três peças novas: o port, o adapter e a composição em `main`.

O que mudou em `app/helpdesk/`:

- `ports.py` (novo): `LLMGateway(Protocol)` com `complete(capability, prompt) -> str`.
- `adapters/gateway.py` (novo): `GatewayClient(LLMGateway)`, que usa o SDK `openai` com `base_url=GATEWAY_URL/v1`, `max_retries=0` e `timeout=25 s` (abaixo dos 30 s da borda).
- `classification.py`: recebe `gateway: LLMGateway`, define `CAPABILITY = "ticket-classify"` e monta o prompt `TASK: classify`. Não importa mais `llm` nem `config`.
- `main.py`: instancia `GatewayClient` (o único import de `adapters`) e o injeta na rota da F1.
- `config.py`: perdeu `CLASSIFICATION_MODEL` e ganhou `GATEWAY_URL`, `GATEWAY_API_KEY` e `GATEWAY_TIMEOUT`.
- Fora de `app/`: serviços `gateway` (LiteLLM v1.102.1) e `db` (Postgres 16) no compose. A chave `sk-helpdesk-app-0001` é criada pelo entrypoint do gateway (`gateway/keys.yaml`).

Verificação ([`docs/evidence/piloto/f1-pelo-gateway.txt`](evidence/piloto/f1-pelo-gateway.txt)):

- `pytest tests/characterization -q` → `19 passed`, com a suíte sem alteração.
- `/admin/calls` com a F1: uma chamada `openai / gpt-fake-large / classify / 200`, o mesmo modelo da v1.
- Com `docker compose stop gateway`, a F1 dá `500` e nenhuma chamada chega ao provider, enquanto a F4, que ainda está no SDK, dá `200`. Isso prova que a F1 depende do gateway, e não mais do SDK do provider.
- `grep -rn adapters app/helpdesk` → só `main.py:4`.

Medição antes (`metrics/results/v1-coupled.csv`) e depois (`docs/evidence/piloto/piloto-f1.csv`, gerado com `python metrics/coupling.py app/helpdesk --name piloto-f1 --out-dir docs/evidence/piloto`):

| component | antes (ca/ce/i/a/d) | depois (ca/ce/i/a/d) | zona de dor depois |
|---|---|---|---|
| adapters.gateway | — | 1/1/0.5/0/0.5 | não |
| classification | 1/3/0.75/0/0.25 | 1/2/0.67/0/0.33 | não |
| config | 6/0/0/0/1 | 6/0/0/0/1 | sim |
| extraction | 1/3/0.75/0/0.25 | 1/3/0.75/0/0.25 | não |
| llm | 4/1/0.2/0/0.8 | 3/1/0.25/0/0.75 | sim |
| main | 0/5/1/0/0 | 0/7/1/0/0 | não |
| ports | — | 2/0/0/1/0 | não (sobre a reta) |
| report | 1/4/0.8/0/0.2 | 1/4/0.8/0/0.2 | não |
| schemas | 5/0/0/0/1 | 5/0/0/0/1 | sim |
| suggestion | 1/3/0.75/0/0.25 | 1/3/0.75/0/0.25 | não |
| tickets | 1/1/0.5/0/0.5 | 1/1/0.5/0/0.5 | não |

Leitura:

- O que o plano previu e se confirmou: `ports` nasceu sobre a Main Sequence (A = 1, I = 0, D = 0), e `llm` perdeu um dependente (Ca 4 → 3). `llm` só sai da zona de dor quando ele deixar de existir, depois da migração de F2–F4.
- O que o plano previu e não se confirmou: as features e o adapter não ficaram "perto da reta com I alto" (ver R2).
- `config` não se moveu: a F1 deixou de importá-lo, mas `main` passou a importá-lo, e o Ca continua 6 (ver R1).

## Revisões

### R1 (2026-10-13, após o piloto): `config` é absorvido pelo ponto de composição

Plano original: enxugar `config.py` e decidir no piloto se ele ficaria como exceção.

O que o piloto mostrou: tirar um uso de uma feature só transferiu o import para `main`, e o Ca continuou 6. Cada feature migrada troca "importo `config` para saber o modelo" por "conheço meu nome de capacidade". O que sobra em `config` depois de F2–F4 é a URL e a chave do gateway, o timeout, o arquivo de tickets e o tamanho do lote. Os três primeiros só interessam à composição, e os dois últimos só a `tickets` e `report`.

Decisão: `config.py` deixa de existir na Etapa 6. A leitura de ambiente passa a ser responsabilidade de `main` (ponto de composição), que injeta os valores no adapter e em `tickets`. Constantes de uma feature só (`CAPABILITY`, `TICKETS_PER_CALL`) ficam na própria feature. O motivo é estrutural, não a fórmula: um módulo que todo mundo importa para ler variáveis de ambiente é exatamente o acoplamento a "como a app é implantada" que a refatoração quer remover. Rejeitado: declarar `config` como estável por natureza, porque ele contém o que mais mudou na história do sistema (chaves, URLs e modelos).

### R2 (2026-10-13, após o piloto): o critério de pronto é a zona de dor, não D ≈ 0

Plano original: features "perto da reta" e adapter "instável, I alto, D baixo".

O que o piloto mostrou: `classification` ficou com D 0,25 → 0,33, porque perdeu dependências (Ce 3 → 2) e continua com um único dependente (`main`). `adapters.gateway` ficou em I = 0,5 / D = 0,5 pela mesma razão: depende só do port (Ce = 1) e só `main` o importa (Ca = 1). As duas coisas são o efeito esperado de uma estrutura correta: folhas concretas, cujo único cliente é o ponto de composição.

Decisão: manter o critério de pronto como está ("nenhum componente na zona de dor fora das exceções"). Não adotar meta de D por componente. Fica registrado que D das features e do adapter fica entre 0,25 e 0,5, e por quê. Rejeitado: criar imports ou dependências para aumentar o I das features ou do adapter (seria desenhar para a fórmula).

Nota de projeto vinda do mesmo ponto: o adapter herda explicitamente de `LLMGateway` (`class GatewayClient(LLMGateway)`), embora `Protocol` aceite tipagem estrutural. A dependência é real (o adapter implementa o port), e sem ela o adapter ficaria com Ce = 0, I = 0 e cairia na zona de dor, o que esconderia na métrica a relação que de fato existe.

### Plano atualizado para a Etapa 6

F4 → F2 → F3 (com `tickets` recebendo o caminho do arquivo por injeção) → remover `config.py` (R1) → remover `llm.py` e `anthropic` do `requirements.txt` → tirar o `env_file` do serviço `app` → medir `v2-decoupled` → tag.

## Etapa 6: escala da refatoração até a `v2-decoupled`

Mesmo padrão do piloto, um componente por vez. Cada passo foi executado com `scripts/refactor_step.sh <passo>`, que reconstrói a app, roda a suíte, mede e mostra `/admin/calls`. As saídas estão em [`docs/evidence/refactor/`](evidence/refactor/).

| Passo | Mudança | Suíte | Efeito na métrica |
|---|---|---|---|
| 1. F4 | `extraction` recebe o gateway e usa `ticket-extract` | 19 passed | `llm` Ca 3 → 2, `config` Ca 6 → 5 |
| 2. F2 | `suggestion` recebe o gateway e usa `ticket-reply` | 19 passed | `llm` Ca 2 → 1, `config` Ca 5 → 4 |
| 3. F3 + tickets | `TicketSource` em `ports`; `tickets.py` vira `adapters/tickets_file.py` (`JsonlTicketSource`); `report` recebe gateway e fonte de tickets e guarda `TICKETS_PER_CALL` | 19 passed | `llm` fica sem dependentes (Ca = 0); `config` Ca 4 → 2; `report` Ce 4 → 2 |
| 4. config/llm (R1) | `config.py` e `llm.py` removidos; leitura do ambiente em `main`; `anthropic` fora do `requirements.txt`; `env_file` e `depends_on: provider-fake` fora do serviço `app` | 19 passed | só `schemas` continua na zona de dor |

Medição final ([`metrics/results/v2-decoupled.csv`](../metrics/results/v2-decoupled.csv)):

| component | ca | ce | i | a | d | zona de dor |
|---|---|---|---|---|---|---|
| adapters.gateway | 1 | 1 | 0.5 | 0 | 0.5 | não |
| adapters.tickets_file | 1 | 1 | 0.5 | 0 | 0.5 | não |
| classification | 1 | 2 | 0.67 | 0 | 0.33 | não |
| extraction | 1 | 2 | 0.67 | 0 | 0.33 | não |
| main | 0 | 7 | 1 | 0 | 0 | não |
| ports | 6 | 0 | 0 | 1 | 0 | não (sobre a reta) |
| report | 1 | 2 | 0.67 | 0 | 0.33 | não |
| schemas | 5 | 0 | 0 | 0 | 1 | sim, exceção declarada ("estável por natureza": tipos de valor do contrato HTTP fixado pela suíte; ADR da métrica na Etapa 9) |
| suggestion | 1 | 2 | 0.67 | 0 | 0.33 | não |

Verificação do critério de pronto ([`docs/evidence/refactor/v2-criterio-de-pronto.txt`](evidence/refactor/v2-criterio-de-pronto.txt)):

1. A suíte passa (`19 passed`) e `git diff v1-coupled -- tests/characterization/` está vazio.
2. A métrica tem só `schemas` na zona de dor, que é a exceção.
3. `grep adapters` encontra só `main.py` (linhas 6 e 7).
4. Os greps de modelos físicos, chaves e `provider-fake` em `app/` voltam vazios; `import anthropic` dentro da app dá `ModuleNotFoundError`.
5. `docker compose exec app env` mostra só `GATEWAY_URL`/`GATEWAY_API_KEY`, e o `docker compose config` do serviço `app` não tem `env_file`.
6. `/admin/calls` registra 4 requisições e 4 chamadas, com as mesmas tarefas e os mesmos `large` da v1 (`gpt-fake-large` para classify/extract, `claude-fake-large` para suggest/topics). Com `anthropic` em `error_500`, a F2 chega ao provider 1 vez, contra 3 na v1: `max_retries=0` na app e `num_retries: 0` no gateway. O status externo continua `500`, como na v1, porque resiliência é a Etapa 7.

### R3 (2026-10-13, passo 3): `tickets` vira porta + adapter, em vez de ficar como módulo concreto

O plano original deixava em aberto se `tickets` entraria em `ports`. O passo 3 mostrou que, sem `config`, `tickets.py` precisaria receber o caminho do arquivo de algum lugar. Ler o caminho dentro dele recriaria o acoplamento ao ambiente, e importar `main` seria um ciclo. Decisão: `TicketSource(Protocol)` em `ports`, a implementação de arquivo em `adapters/tickets_file.py` e a composição em `main`, o mesmo padrão do gateway. A F3 passa a depender só de abstrações (`LLMGateway`, `TicketSource`). Isso tem efeito prático: o relatório pode ser testado ou rodado sobre outra fonte de tickets sem tocar em `report.py`, o que vai ajudar na F3 assíncrona da Etapa 8.

## Diário do handoff

| Passo | O que o agente propôs/fez | Evidência apresentada | Decisão do autor |
|---|---|---|---|
| Gateway | LiteLLM v1.102.1 + Postgres; chave da app criada pelo entrypoint (idempotente); `store_model_in_db: false` | logs `chave helpdesk-app criada` e depois `atualizada` após `restart gateway` (8 s, app sem reiniciar); `401` com chave errada; `403` pedindo `gpt-fake-large` direto; `ticket-reply` idêntico ao golden | aceito |
| Piloto F1 | `ports.py`, `adapters/gateway.py`, F1 com gateway injetado, composição em `main` | `19 passed`; `/admin/calls` `gpt-fake-large classify`; F1 `500` com o gateway parado; tabela antes/depois | aceito |
| Revisões R1/R2 | dissolver `config` em `main`; manter o critério pela zona de dor | métrica do piloto (Ca de `config` inalterado; D das folhas explicado) | aceito |
| Rejeitado pelo agente | `GatewayClient` sem herdar de `LLMGateway` (tipagem estrutural) | escondia uma dependência real e jogava o adapter na zona de dor | — |
| Etapa 6, passos 1–3 | F4, F2 e F3 migradas uma por vez; `TicketSource` + `JsonlTicketSource` (R3) | `19 passed` em cada passo; `/admin/calls` com o `large` esperado; CSV por passo em `docs/evidence/refactor/` | aceito |
| Etapa 6, passo 4 | remover `config.py`, `llm.py`, `anthropic` e `env_file` da app | 1ª execução: o `git rm` falhou (arquivo com mudanças locais), e o passo mediu `config`/`llm` ainda presentes, contradizendo a mudança declarada. A medição denunciou o erro; corrigido com `git rm -f` e o passo refeito | rejeitada a 1ª execução, aceita a 2ª |
| Critério de pronto | greps, env, compose config, `/admin/calls` | `docs/evidence/refactor/v2-criterio-de-pronto.txt` | aceito → tag `v2-decoupled` |
| Rejeitado pelo agente | manter `config.py` com só os valores do gateway como "estável por natureza" | contrariava a R1; a função dele (ler o ambiente) pertence à composição | — |
