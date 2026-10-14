# 0002. Colocar um AI Gateway self-hosted (LiteLLM) entre a app e os providers

- Status: aceita
- Data: 2026-10-13
- Nível: solução

## Contexto

Na v1, a app dependia de tudo o que é volátil nos providers: SDK, formato da API, URL, credencial e nome do modelo. `llm.py` ficava na zona de dor da métrica (Ca 4, D 0,8). Trocar de modelo exigia rebuild (D4). Retry e timeout eram os padrões dos SDKs (600 s, 2 retries) e, sem fallback, uma queda da Anthropic derrubava F2 e F3 (D3). Os requisitos pedem nomes lógicos, credenciais fora da app, troca de modelo só por configuração, orçamento e limites recusados antes do provider, e resiliência declarada.

## Opções consideradas

1. Biblioteca na app (ex.: SDK LiteLLM ou um cliente próprio com fallback): resolve o formato, mas as credenciais continuam na app, e trocar o modelo continua exigindo deploy da app.
2. Gateway self-hosted como serviço próprio (LiteLLM Proxy), na frente dos providers.
3. Gateway SaaS: depende de rede externa e de chave paga, o que contraria a restrição de rodar inteiro contra o provider simulado.

## Decisão

Opção 2, com o LiteLLM Proxy na imagem fixada `ghcr.io/berriai/litellm:v1.102.1`, a versão testada pelo README do provider-fake. Ele fica na posição de serviço `gateway` do compose, entre `app` e `provider-fake`, com a configuração versionada em `gateway/`. O serviço expõe a API Chat Completions, então a app usa um único SDK (`openai`) apontado para ele. Retry, timeout, fallback, preço, orçamento e limites moram no gateway. A app desliga o próprio retry (`max_retries=0`) para não multiplicar tentativas.

## Consequências

- Credencial, modelo físico, resiliência e governança ficam num único lugar, e a app passa a conhecer só o endereço e a chave do gateway.
- Uma peça a mais para operar, com banco (ADR 0007). O gateway parado derruba as features de IA, e a app responde `503` explícito.
- A porta `4000` fica exposta no host para o roteiro de governança. As rotas administrativas exigem a master key, e as chaves são do provider simulado. Em produção, ela não seria publicada.

## Evidência

```
grep -rE "gpt-fake|claude-fake|anthropic" app/                       # vazio
docker compose exec app env | grep -iE "KEY|GATEWAY"                  # só GATEWAY_URL e GATEWAY_API_KEY
grep -rn "max_retries" app/helpdesk/adapters/gateway.py               # max_retries=0
docker compose stop gateway; curl -s -X POST localhost:8000/tickets/classification \
  -H "Content-Type: application/json" -d '{"ticket_id":"T","text":"pedido"}' -w " %{http_code}\n"   # 503, nada em /admin/calls
docker compose start gateway
```

A troca de modelo só pelo gateway está registrada em `docs/evidence/troca-de-modelo/f1-large-para-mini.txt`.
