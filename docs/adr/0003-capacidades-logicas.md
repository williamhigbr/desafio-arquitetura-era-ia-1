# 0003. Expor à app quatro capacidades lógicas, uma por feature, mapeadas no gateway

- Status: aceita
- Data: 2026-10-13
- Nível: solução

## Contexto

A app precisa pedir "o que" (classificar, extrair, sugerir, resumir temas), e não "com quem" (`gpt-fake-large`). Cada feature tem uma necessidade diferente: latência, tamanho de saída, tolerância a um modelo fraco (ADR 0005) e modo de execução (ADR 0004). Por isso, cada feature precisa de timeout e cadeia de fallback próprios, mesmo quando usa o mesmo modelo físico de outra.

## Opções consideradas

1. Um nome lógico por modelo físico (ex.: `large-openai`). A app continuaria escolhendo provider e tamanho.
2. Um nome lógico por tipo de modelo (`fast`, `smart`). Isso mistura features com necessidades diferentes, e a F4 não pode cair para `mini`, mas a F1 pode.
3. Um nome lógico por capacidade de negócio, ou seja, por feature.

## Decisão

Opção 3. As capacidades são `ticket-classify` (F1), `ticket-extract` (F4), `ticket-reply` (F2) e `topics-report` (F3, por lote). Cada uma aponta para o `large` que a feature usava na v1, para que a suíte de caracterização continue passando. A cadeia de cada capacidade tem grupos auxiliares (`<capacidade>-alt` para o fallback técnico e `<capacidade>-mini` para o modelo fraco), que a app não conhece e nunca chama. Cada feature guarda o próprio nome lógico como constante (`CAPABILITY`).

| Capacidade | Primário | Fallback técnico | Modelo fraco |
|---|---|---|---|
| `ticket-classify` | `openai/gpt-fake-large` | `anthropic/claude-fake-large` | `openai/gpt-fake-mini` |
| `ticket-extract` | `openai/gpt-fake-large` | `anthropic/claude-fake-large` | — |
| `ticket-reply` | `anthropic/claude-fake-large` | `openai/gpt-fake-large` | `anthropic/claude-fake-mini` |
| `topics-report` | `anthropic/claude-fake-large` | `openai/gpt-fake-large` | `anthropic/claude-fake-mini` |

## Consequências

- Trocar o modelo de uma feature é editar uma linha de `gateway/config.yaml` e reiniciar só o gateway. As outras features não são afetadas.
- Uma feature nova precisa de uma capacidade nova no gateway. Isso é intencional: o mapeamento e a política nascem junto com a feature.
- As chaves dão acesso ao grupo `helpdesk` (todas as capacidades). Como o LiteLLM só segue um fallback para grupos que a chave pode chamar, os grupos auxiliares também ficam no grupo de acesso.

## Evidência

```
grep -rn "CAPABILITY =" app/helpdesk          # ticket-classify, ticket-extract, ticket-reply, topics-report
grep -rE "gpt-fake|claude-fake" app/          # vazio
scripts/swap_model.sh                         # F1 large -> mini só com restart do gateway; app com o mesmo StartedAt
```

Saída: `docs/evidence/troca-de-modelo/f1-large-para-mini.txt` (antes, `gpt-fake-large classify 200`; depois do `restart gateway`, `gpt-fake-mini classify 200`, com a app iniciada no mesmo instante).
