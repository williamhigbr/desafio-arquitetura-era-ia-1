#!/usr/bin/env python3
"""Compara as 4 tarefas do provider-fake nos 4 modelos (large/mini x openai/anthropic).

Chama o provider direto (sem a app), com o mesmo conteúdo em todos os modelos, e grava
as saídas brutas em JSON para servir de evidência ao ADR de fallback.

Uso (com o compose no ar):
    python3 scripts/compare_models.py [--out docs/evidence/model-comparison.json]

Só usa a biblioteca padrão. Lê as chaves do .env da raiz (chaves do provider simulado).
"""
import argparse
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROVIDER = "http://localhost:8090"
MODELS = [
    ("openai", "gpt-fake-large"),
    ("openai", "gpt-fake-mini"),
    ("anthropic", "claude-fake-large"),
    ("anthropic", "claude-fake-mini"),
]

# Tickets reais de data/tickets.jsonl, escolhidos para exercitar casos diferentes.
TICKET_IDS = [
    "TK-00002",  # nota fiscal + pedido "#NNNNNN" + produto genérico
    "TK-00015",  # nota fiscal + pedido sem "#"
    "TK-00036",  # nota fiscal + pedido "nº" + produto nomeado
    "TK-00004",  # só pedido "nº", sem produto
    "TK-00001",  # sem número de pedido
    "TK-00007",  # sem pedido, com produto nomeado
]
EXTRA_TEXTS = {
    "AD-HOC-1": "Meu pedido #481516 não chegou e já passou do prazo, urgente",
    "AD-HOC-2": "NF 123456, pedido #654321, fone de ouvido bluetooth com defeito",
}
TOPICS_BATCH = 150  # mesmo tamanho de lote da app (config.TICKETS_PER_CALL)


def load_env() -> dict:
    env = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def call(provider: str, model: str, content: str, env: dict) -> dict:
    if provider == "openai":
        url = f"{PROVIDER}/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {env['FAKE_OPENAI_KEY']}"}
        body = {"model": model, "messages": [{"role": "user", "content": content}]}
    else:
        url = f"{PROVIDER}/anthropic/v1/messages"
        headers = {"x-api-key": env["FAKE_ANTHROPIC_KEY"], "anthropic-version": "2023-06-01"}
        body = {"model": model, "max_tokens": 2000, "messages": [{"role": "user", "content": content}]}
    headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    started = time.monotonic()
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.load(resp)
    elapsed = round(time.monotonic() - started, 2)
    if provider == "openai":
        text, usage = data["choices"][0]["message"]["content"], data["usage"]
    else:
        text, usage = data["content"][0]["text"], data["usage"]
    return {"output": text, "usage": usage, "seconds": elapsed}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="docs/evidence/model-comparison.json")
    args = parser.parse_args()

    env = load_env()
    tickets = [json.loads(line) for line in (ROOT / "data/tickets.jsonl").open(encoding="utf-8")]
    by_id = {t["id"]: t for t in tickets}
    texts = {tid: by_id[tid]["text"] for tid in TICKET_IDS} | EXTRA_TEXTS

    cases = []  # (task, input_id, content)
    for task in ("classify", "suggest", "extract"):
        for input_id, text in texts.items():
            cases.append((task, input_id, f"TASK: {task}\n{text}"))
    batch = "\n".join(f"[{t['id']}] {t['text']}" for t in tickets[:TOPICS_BATCH])
    cases.append(("topics", f"first-{TOPICS_BATCH}-tickets", f"TASK: topics\n{batch}"))

    jobs = [(c, m) for c in cases for m in MODELS]
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda j: call(j[1][0], j[1][1], j[0][2], env), jobs))

    report = []
    for ((task, input_id, content), (provider, model)), res in zip(jobs, results):
        report.append({
            "task": task, "input_id": input_id,
            "input": texts.get(input_id, f"<{TOPICS_BATCH} primeiros tickets>"),
            "provider": provider, "model": model, **res,
        })

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(report)} chamadas gravadas em {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
