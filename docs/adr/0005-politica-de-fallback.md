# 0005. Fallback técnico para o large do outro provider em todas as features; modelo fraco só em F1, F2 e F3

- Status: aceita
- Data: 2026-10-13
- Nível: solução

## Contexto

Na v1, cada feature estava presa a um provider, e o retry do SDK repetia no mesmo destino caído: 3 chamadas com `500` e nenhuma tentativa no outro provider (D3). O provider simulado declara que os dois `large` são equivalentes (mesma saída), e que os `mini` são mais rápidos e mais baratos, com qualidade inferior em pelo menos uma tarefa. A escolha do modelo fraco é uma decisão de produto: a pergunta é se uma resposta pior vale mais do que nenhuma, para quem consome aquela resposta.

Evidência coletada na Etapa 1 (`docs/evidence/etapa1-reconhecimento.md`, dados brutos em `docs/evidence/model-comparison.json`; mesmas 8 entradas nos 4 modelos):

| Tarefa | `large` openai = `large` anthropic | `mini` = `large` | O que muda no `mini` |
|---|---|---|---|
| classify | 8/8 | 8/8 | nada |
| topics | 1/1 (lote de 150) | 1/1 | nada (mesmos temas, contagens e exemplos) |
| suggest | 8/8 | 0/8 | mesmos pontos, ~45% do tamanho (~1.070 contra ~2.430 caracteres) |
| extract | 8/8 | 3/8 | pega o 1º número de 6 dígitos (a nota fiscal) como pedido e nunca devolve `product` |

## Opções consideradas

1. Só retry, sem fallback.
2. Fallback técnico (o outro `large`) para todas as features, e o `mini` como último destino para todas.
3. Fallback técnico para todas as features, e o `mini` decidido feature por feature.

## Decisão

Opção 3. A cadeia de cada capacidade é primário → `large` do outro provider → (se a feature aceitar) `mini`. A política de retry por destino é 1 retry em `5xx` com backoff, 2 em `429` respeitando o `Retry-After`, e nenhum em timeout, que vai direto ao próximo destino. Sem cooldown, para que cada requisição percorra a cadeia inteira.

### F1, classificação: aceita `mini`

O `mini` deu a mesma saída do `large` em 8 de 8 entradas, então não há custo de qualidade observado. Responder com `mini` mantém o roteamento do ticket funcionando.

### F2, sugestão: aceita `mini` (degradação declarada)

O `mini` gera uma sugestão com os mesmos pontos e cerca de 45% do tamanho. Quem consome é um atendente, que lê e edita antes de enviar. Um rascunho mais curto é melhor que nenhum.

### F3, relatório: aceita `mini`

No lote de 150 tickets, o `mini` produziu temas, contagens e exemplos idênticos aos do `large`. O relatório completo sai igual e mais rápido (2,7 s por lote contra 9,8 s).

### F4, extração: não aceita `mini`, falha explicitamente

O erro do `mini` é silencioso e plausível. Em "nota fiscal 927477 … pedido nº 389511", ele devolve `#927477`, que tem formato válido e passa pela validação da app (`^#\d{6}$`). Ele também perde o produto. 717 dos 5.000 tickets (14%) citam nota fiscal. A F4 alimenta uma automação sem revisão humana, e um pedido errado vira troca ou devolução do pedido errado. Por isso, sem `large` disponível, a F4 responde `503` com `Retry-After: 30` e `{"detail": "Serviço de IA indisponível...", "capability": "ticket-extract"}`, e quem chama tenta de novo depois.

## Consequências

- O pior caso de latência cresce com a cadeia: F1 sem nenhum provider leva 2+2+2 tentativas e ~8 s até o `503`, ainda abaixo da borda e dos 15 s.
- Quando o `mini` responde, a resposta não diz isso ao cliente. A degradação fica visível no gateway (header `x-litellm-model-group`) e em `/admin/calls`.
- Se o `mini` melhorar na extração, a decisão da F4 pode ser revista com uma nova comparação (`python3 scripts/compare_models.py`).

## Evidência

`python3 scripts/resilience.py large-500 anthropic-500 openai-500 all-down` (saídas em `docs/evidence/resiliencia/`):

| Cenário | F1 | F2 | F3 | F4 |
|---|---|---|---|---|
| primário em `error_500` | `200`; gpt-large 2 → claude-large 1 | `200`; claude-large 2 → gpt-large 1 | `202` → `303`; claude-large 2 → gpt-large 1 | `200`; gpt-large 2 → claude-large 1 |
| os dois `large` em `error_500` | `200` com gpt-mini (2 → 2 → 1) | `200`, stream com 1.096 caracteres, claude-mini (2 → 2 → 1) | `202` → `303`, relatório igual, claude-mini | `503` (2 → 2, nunca o mini) |
| os dois providers em `error_500` | `503` | `503` | `failed` com motivo em ~6 s | `503` |
