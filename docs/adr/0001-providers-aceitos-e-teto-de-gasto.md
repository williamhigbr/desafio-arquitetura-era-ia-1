# 0001. Aceitar só OpenAI e Anthropic, sempre via gateway, com teto de US$ 100/mês para a IA do helpdesk

- Status: aceita
- Data: 2026-10-13
- Nível: corporativa

## Contexto

Na v1, cada feature escolheu um provider "porque funcionou melhor no teste", e as chaves ficavam na aplicação (`app/helpdesk/config.py`). Nada limitava o gasto: o relatório mensal consumiu ~222 mil tokens (≈ US$ 0,85) mesmo depois de a borda ter desistido, e repetir o pedido repetia o custo inteiro (Diagnóstico, D1). A decisão de quais empresas recebem os dados dos tickets e de quanto se aceita gastar não é do time que escreve o código. É uma decisão da empresa, de compliance e de orçamento, e ela precisa sobreviver a trocas de modelo.

Estimativa de consumo mensal com os preços simulados, para ~5.000 tickets/mês:

- classificação e extração: 5.000 × ~US$ 0,00016 × 2 ≈ US$ 1,60;
- uma sugestão por ticket: 5.000 × ~US$ 0,0092 ≈ US$ 46;
- alguns relatórios mensais a ~US$ 0,85 cada.

Total ≈ US$ 50/mês.

## Opções consideradas

1. Sem política: cada time escolhe provider e assume o gasto.
2. Lista de providers aprovados e teto mensal, aplicados no ponto único de saída para os providers (o gateway).
3. Um único provider para a empresa inteira.

## Decisão

Opção 2. Os providers aprovados são OpenAI e Anthropic, os dois do contrato do helpdesk. Nenhuma aplicação os chama diretamente: o acesso é sempre pelo AI Gateway (ADR 0002), que é o único lugar com as credenciais. A opção 3 foi rejeitada porque um provider só elimina o fallback técnico (ADR 0005), que é o que mantém o helpdesk no ar quando um provider cai. O teto de gasto com IA do helpdesk é de US$ 100 a cada 30 dias, cerca de 2 vezes o consumo estimado. Ele fica na chave do gateway usada pela app (`max_budget: 100`, `budget_duration: 30d`). Acima dele, o gateway recusa a chamada antes de chegar ao provider.

## Consequências

- Um provider novo exige revisar este ADR, e não só a configuração.
- Ao atingir o teto, as features de IA passam a responder `503` até o orçamento renovar. É uma indisponibilidade consciente, e o alerta e o pedido de aumento são processos da empresa.
- O teto vale por chave. Outra aplicação que use o gateway precisa da sua própria chave e do seu próprio teto.

## Evidência

```
grep -rE "FAKE_OPENAI_KEY|FAKE_ANTHROPIC_KEY|sk-fake-openai|sk-ant-fake" app/      # vazio
grep -n "api_base" gateway/config.yaml                                            # só provider-fake/openai e provider-fake/anthropic
set -a; . ./.env; set +a
curl -s "localhost:4000/key/info?key=$GATEWAY_APP_KEY" -H "Authorization: Bearer $LITELLM_MASTER_KEY"
# ... "key_alias": "helpdesk-app", "max_budget": 100.0, "budget_duration": "30d", "budget_reset_at": "2026-11-01T00:00:00+00:00"
```

O mecanismo de recusa por orçamento antes do provider está provado no roteiro de governança (ADR 0006, `docs/evidence/governanca/roteiro.txt`).
