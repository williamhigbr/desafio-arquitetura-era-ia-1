# 0007. Usar Postgres (serviço `db`) como banco do gateway

- Status: aceita
- Data: 2026-10-13
- Nível: software

## Contexto

O requisito 5 pede uma chave da app que não seja a de um provider, uma chave com orçamento e uma com limite de requisições. No LiteLLM, chaves virtuais, gasto por chave e `max_budget`/`rpm_limit` por chave só existem com um banco conectado (`DATABASE_URL`). Sem banco, só há a master key, e nenhum desses controles. O LiteLLM usa Prisma, que suporta Postgres.

## Opções consideradas

1. Sem banco, só com a master key: não atende à governança por chave.
2. Postgres no compose (`postgres:16-alpine`), com volume nomeado.
3. Postgres gerenciado fora do compose: contraria "sobe tudo com um comando".

## Decisão

Opção 2. O serviço `db` fica acessível só na rede do compose, sem porta publicada, com senha do `.env` e volume `gateway-db`. O gateway só sobe depois de `db` estar saudável (`depends_on: service_healthy`), e as migrações rodam na subida do próprio LiteLLM. As chaves de `gateway/keys.yaml` são reaplicadas a cada start do gateway (criadas ou atualizadas), então o estado delas não depende do que estiver no banco. A lista de modelos continua vindo só do arquivo (`store_model_in_db: false`).

## Consequências

- Mais uma peça para operar (backup e upgrade), com ~20 s a mais na primeira subida por causa das migrações.
- O gasto acumulado por chave persiste entre restarts do gateway (volume). `docker compose down -v` zera tudo.

## Evidência

```
docker compose ps db                        # healthy, sem porta publicada
docker compose logs gateway | grep -E "migrat|provision_keys"   # migrações aplicadas; chaves criadas/atualizadas
docker compose restart gateway && docker compose logs --since 1m gateway | grep provision_keys   # "atualizada"
```

O roteiro de governança (ADR 0006) depende do banco e funciona.
