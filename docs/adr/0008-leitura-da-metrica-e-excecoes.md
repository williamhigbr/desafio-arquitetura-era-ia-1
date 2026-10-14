# 0008. Leitura da métrica A × I e exceção declarada (`schemas`, estável por natureza)

- Status: aceita
- Data: 2026-10-13
- Nível: software

## Contexto

A régua é a do requisito 3, implementada em `metrics/coupling.py` (com testes em `metrics/tests/`). Ela coloca na zona de dor todo componente concreto e muito usado, com A < 0,5, I < 0,5 e D ≥ 0,5. Na `v1-coupled` havia três componentes nessa zona: `llm` (D 0,8), `config` (D 1) e `schemas` (D 1). A métrica é o critério de pronto da refatoração, mas a fórmula não sabe quais componentes de fato mudam.

## Opções consideradas

1. Zerar a zona de dor a qualquer custo, inclusive com imports ou classes artificiais.
2. Corrigir os componentes em que estar na zona é um risco real e declarar, com justificativa, os que só caem lá pela fórmula.

## Decisão

Opção 2.

- `llm` era um risco real: concentrava SDK, provider, formato e credencial, tudo volátil, e as 4 features dependiam dele. Foi dividido entre `ports.LLMGateway` (A = 1, I = 0, sobre a reta) e `adapters.gateway`.
- `config` também era um risco real: misturava credenciais, URLs e modelos. Foi absorvido pelo ponto de composição (`main`), na revisão R1 do plano.
- `schemas` é declarado estável por natureza. São 7 classes pydantic com os tipos de valor do contrato HTTP (`TicketInput`, `Classification`, `TopicsReport`...). Elas só mudam se o contrato mudar, e o contrato é fixado pelas suítes de caracterização e de contrato. Transformá-las em abstrações não teria sentido: são dados, não comportamento.

Não podem ser exceção, e não são: `adapters.gateway`, o componente que chama o gateway, e as features (`classification`, `extraction`, `suggestion`, `report`).

Leitura das três medições:

| Versão | Zona de dor | O que mudou |
|---|---|---|
| `v1-coupled` | `llm`, `config`, `schemas` | A = 0 em todos; tudo depende de concretos |
| `v2-decoupled` | só `schemas` | `ports` (A = 1) sobre a reta; features e adapters são folhas concretas com um único cliente (`main`), em I 0,5–0,67 |
| `main` | só `schemas` | `ports` ganha `JobStore`, e `adapters.memory_jobs` entra no mesmo padrão dos outros adapters (I 0,5, D 0,5) |

As features ficam em D 0,33 e os adapters em D 0,5, e isso não é tratado como dívida. São folhas concretas cujo único dependente é o ponto de composição. Criar dependências para empurrá-las para a reta seria desenhar para a fórmula (revisão R2 do plano).

## Consequências

- Um componente novo na zona de dor precisa ser corrigido ou entrar neste ADR com justificativa. A lista de exceções não cresce por padrão.
- Se `schemas` começar a ganhar lógica (validação de negócio, conversões), deixa de ser "tipo de valor" e esta exceção precisa ser revista.

## Evidência

```
python metrics/coupling.py app/helpdesk --name main                 # metrics/results/main.csv
git worktree add ../v1 v1-coupled && python metrics/coupling.py ../v1/app/helpdesk --name v1-coupled \
  && git diff --exit-code metrics/results/v1-coupled.csv; git worktree remove ../v1   # reproduz o CSV versionado
pytest metrics/tests -q
```

Em `metrics/results/main.csv`, a única linha com i < 0,5, a < 0,5 e d ≥ 0,5 é `schemas,5,0,0,0,1`.
