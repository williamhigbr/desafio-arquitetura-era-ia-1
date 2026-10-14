# 0006. Governança por chave virtual: orçamento e limite de requisições aplicados no gateway

- Status: aceita
- Data: 2026-10-13
- Nível: solução

## Contexto

O ADR 0001 fixa um teto de gasto da empresa, mas um teto precisa de um mecanismo que recuse a chamada antes de o token ser pago. Registrar o gasto depois não governa nada. Na v1 não havia nenhum: o relatório abandonado continuou gastando (D1). O nível é "solução", e não "corporativo": o valor do teto é corporativo, mas como e onde aplicá-lo (por chave, no gateway, com qual janela) é decisão desta solução e muda com ela.

## Opções consideradas

1. Contagem e limite na app: cada app reimplementaria a regra, e ela ficaria longe do ponto onde o custo é conhecido.
2. Orçamento e limite por chave virtual no gateway, com os preços dos modelos declarados na configuração.
3. Alertas de billing no provider: chegam depois do gasto e não recusam nada.

## Decisão

Opção 2. Cada consumidor recebe a própria chave virtual, criada automaticamente a partir de `gateway/keys.yaml` a cada subida:

| Chave | Uso | Controle |
|---|---|---|
| `helpdesk-app` | app | `max_budget` US$ 100 a cada 30 dias (ADR 0001) |
| `governance-budget` | demonstração do orçamento | `max_budget` US$ 0,0002 (~1 classificação) |
| `governance-rpm` | demonstração do limite de requisições | `rpm_limit` 2 |

Os preços por token vêm do README do provider-fake e estão em `gateway/config.yaml`. Sem eles, o LiteLLM calcula custo zero e nenhum orçamento estoura.

## Consequências

- O gasto é contabilizado depois da resposta, então uma chave pode passar do teto em no máximo o custo de uma chamada. A recusa vem na chamada seguinte.
- O limite de rpm vale por chave, numa janela de um minuto. Ele não protege o provider de outras chaves, e isso continua sendo papel do retry e do fallback.
- Preço de modelo novo precisa entrar no `config.yaml` junto com o modelo, ou o gasto dele não conta.

## Evidência

`scripts/governance.sh` (saída em `docs/evidence/governanca/roteiro.txt`):

| Chave | Chamada | Gateway | `/admin/calls` |
|---|---|---|---|
| `governance-budget` | 1ª, 2ª | `200` | 0 → 1 → 2 |
| `governance-budget` | 3ª, 4ª | `429 budget_exceeded` (Current cost 0.000295 > Max budget 0.0002) | 2 → 2 |
| `governance-rpm` | 1ª, 2ª | `200` | 2 → 3 → 4 |
| `governance-rpm` | 3ª, 4ª | `429 throttling_error` (Current limit: 2, Remaining: 0) | 4 → 4 |
