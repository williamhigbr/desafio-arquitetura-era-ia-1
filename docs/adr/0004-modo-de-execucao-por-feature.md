# 0004. Modo de execução por feature: F1 e F4 síncronas, F2 em streaming, F3 assíncrona

- Status: aceita
- Data: 2026-10-13
- Nível: solução

## Contexto

A borda encerra com `504` uma requisição que passa 30 s sem enviar bytes. Na v1, tudo era síncrono: o relatório do mês dava `504` e continuava gastando tokens (D1), e a sugestão deixava a tela parada por 16 s (D2). Os números medidos na Etapa 1 são estes:

| Feature | Saída | Latência no `large` |
|---|---|---|
| F1 | ~11 tokens | ~1,1 s |
| F4 | ~11 tokens | ~1,1 s |
| F2 | ~610 tokens | primeiro token em 0,8 s, total ~16 s |
| F3 | 5.000 tickets em 34 lotes de 150 | ~9,8 s por lote |

A árvore de decisão aplicada a cada feature: alguém está esperando agora? Cabe no orçamento de latência? É consumível aos poucos?

## Opções consideradas

Para cada feature: síncrono com timeout, streaming (SSE) ou Async Request-Reply (`202` + polling).

## Decisão

### F1, classificação: síncrono com timeout

- Alguém espera? Sim, o fluxo de atendimento precisa da categoria para seguir.
- Cabe na latência? Sim: ~1,1 s contra 3 s exigidos.
- Consumível aos poucos? Não faz sentido, porque são dois campos de um JSON.

Síncrono, com timeout de 5 s por tentativa no gateway. No pior caso declarado (primário em `timeout`): 5 s + ~1,1 s do fallback ≈ 6 s, abaixo dos 15 s.

### F2, sugestão de resposta: streaming SSE

- Alguém espera? Sim, o atendente com o ticket aberto.
- Cabe na latência? Inteira, não: ~16 s de geração. O primeiro trecho sim, em ~0,8 s.
- Consumível aos poucos? Sim, o atendente lê e começa a editar o texto enquanto ele chega.

Streaming SSE, que é o padrão para texto longo lido por um humano. O fim da janela de 30 s da borda é consequência, não motivo: bytes chegam a cada fração de segundo. O contrato:

- `data: {"chunk": ...}` para cada trecho;
- `event: end` com o `ticket_id` no fim;
- `event: error` quando há falha depois do `200`.

A rota abre o stream no gateway antes de responder, então uma falha total (nenhum destino) ainda vira `503`, e não um stream vazio. A app envia `X-Accel-Buffering: no` para a borda não acumular a resposta. No gateway, `stream_timeout: 4` troca de destino se o primeiro byte não vier em 4 s.

### F3, relatório de temas: assíncrono (Async Request-Reply)

- Alguém espera? Não. É um relatório de painel, pedido e consultado depois, sem ninguém olhando a tela.
- Cabe na latência? Não: 34 lotes levam minutos (~5,5 min em série).
- Consumível aos poucos? Não. Temas e contagens só fazem sentido com o mês inteiro agregado.

A justificativa é "ninguém está esperando e o trabalho não cabe no orçamento de latência". Streaming manteria a conexão aberta por minutos para entregar algo que só é útil no fim. O fluxo:

1. `POST /reports/topics` → `202` com `Location: /reports/topics/status/{id}` e `Retry-After: 5`.
2. `GET` no status → `200 {"state": "running", "progress": "1800/5000"}` enquanto processa, `200 {"state": "failed", "reason": ...}` em caso de falha, e `303` para `/reports/topics/{id}` quando termina.

A execução roda numa thread da própria app, com 4 lotes em paralelo. Isso reduz o mês de ~5,5 min para ~1,5 min, sem rajada contra o provider. O estado fica em memória, atrás da porta `JobStore` (`adapters/memory_jobs.py`). Se um lote falhar depois dos fallbacks do gateway, o trabalho vai para `failed` na hora. Um prazo de 600 s garante que nada fique preso em `running`.

### F4, extração: síncrono com timeout

- Alguém espera? Uma automação que precisa do JSON para seguir com a troca ou devolução.
- Cabe na latência? Sim, ~1,1 s.
- Consumível aos poucos? Não, é um JSON curto.

Síncrono, timeout de 5 s por tentativa, como a F1.

## Consequências

- O contrato de F2 e F3 muda na `main`. `tests/characterization/` continua igual ao das tags (juiz da refatoração), e `tests/contract/` fixa o contrato novo.
- O assíncrono em processo exige um único processo da app (o uvicorn padrão, 1 worker). O estado se perde num restart, e várias réplicas veriam estados diferentes. Fila, worker separado ou banco passariam a ser necessários se houver múltiplas réplicas, se for preciso sobreviver a restart ou se o volume justificar escalar o processamento separadamente. Hoje nenhum requisito pede isso, e trocar o adapter não toca na feature.
- Numa falha de lote, os lotes ainda não iniciados são cancelados. Os que já estavam rodando terminam e são descartados, com no máximo 3 chamadas desperdiçadas.

## Evidência

Suíte `tests/contract/` (17 testes, ~3 min), pela borda:

```
pytest tests/contract -q
```

Saídas observadas (`docs/evidence/resiliencia/`):

- F1: `200` em 1,2 s; com o primário em `timeout`, `200` em 6,2 s.
- F2:
  - primeiro trecho em 0,8–0,9 s e 613 trechos em ~17,8 s, com `Content-Type: text/event-stream` e o texto concatenado igual ao golden da v1;
  - com `anthropic` em `midstream_error`, 153 trechos e depois `event: error` com `{"ticket_id": "TK-00042", "message": "A geração foi interrompida"}`;
  - com o primário em `timeout`, o primeiro trecho chega em ~5 s, pelo fallback.
- F3:
  - o mês é aceito em 0,03 s (`202`), o progresso avança de 600 em 600 e termina com `303` em ~88 s, com `total_tickets: 5000` e 34 chamadas `topics` em `/admin/calls`;
  - com os dois providers em `error_500`, `failed` com motivo em 6,1 s.
- Nenhum cenário produziu `504` da borda.
