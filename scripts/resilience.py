#!/usr/bin/env python3
"""Cenários de resiliência pela borda, contando tentativas por destino em /admin/calls.

Uso (com o compose no ar):
    python3 scripts/resilience.py [cenario ...]        # sem argumentos: todos
Cenários: normal, anthropic-500, openai-500, large-500, timeout-f1, rate-limit-f1, all-down

Grava docs/evidence/resiliencia/<cenario>.txt. Só biblioteca padrão.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

EDGE = "http://localhost:8000"
FAKE = "http://localhost:8090"
OUT = Path(__file__).resolve().parent.parent / "docs/evidence/resiliencia"
TICKET = {"ticket_id": "TK-00036", "text": "Tenho aqui a nota fiscal 927477. Recebi o tapete de yoga e o "
          "aparelho não liga, pedido nº 389511. É só uma dúvida. Prefiro contato por e-mail. Podem me ajudar, por favor?"}
FEATURES = {
    "F1": ("/tickets/classification", TICKET, "classify"),
    "F4": ("/tickets/extraction", TICKET, "extract"),
    "F2": ("/tickets/reply-suggestion", TICKET, "suggest"),
    "F3": ("/reports/topics", {"start": "2026-08-01", "end": "2026-08-01"}, "topics"),
}


def http(method, url, body=None, timeout=60):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode(), time.monotonic() - started
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), time.monotonic() - started


def fail(*modes):
    http("POST", f"{FAKE}/admin/reset")
    for mode in modes:
        http("POST", f"{FAKE}/admin/failures", mode)


def summary(body: str, feature: str) -> str:
    try:
        data = json.loads(body)
    except ValueError:
        return body[:80].replace("\n", " ")
    if feature == "F2" and "suggestion" in data:
        return f"suggestion com {len(data['suggestion'])} caracteres"
    if feature == "F3" and "topics" in data:
        return f"total_tickets={data['total_tickets']}, {len(data['topics'])} temas"
    return json.dumps({k: v for k, v in data.items() if k != "ticket_id"}, ensure_ascii=False)


def run(feature: str, lines: list[str]) -> None:
    route, body, task = FEATURES[feature]
    before = {c["id"] for c in json.loads(http("GET", f"{FAKE}/admin/calls?last=5000")[1])}
    status, resp, seconds = http("POST", EDGE + route, body)
    time.sleep(0.3)
    calls = [c for c in json.loads(http("GET", f"{FAKE}/admin/calls?last=5000")[1])
             if c["id"] not in before and c["task"] == task]
    calls.reverse()
    per_dest = Counter(c["model"] for c in calls)
    seq = " → ".join(f"{c['model']}:{c['status']}" for c in calls)
    lines.append(f"### {feature} {route}")
    lines.append(f"  HTTP {status} em {seconds:.2f} s pela borda: {summary(resp, feature)}")
    lines.append(f"  tentativas por destino: {dict(per_dest)}")
    lines.append(f"  sequência em /admin/calls: {seq}")


SCENARIOS = {
    "normal": ("provider normal", [], ["F1", "F4", "F2", "F3"]),
    "anthropic-500": ("anthropic inteiro em error_500 (primário de F2/F3)",
                      [{"provider": "anthropic", "mode": "error_500"}], ["F2", "F3", "F1"]),
    "openai-500": ("openai inteiro em error_500 (primário de F1/F4)",
                   [{"provider": "openai", "mode": "error_500"}], ["F1", "F4"]),
    "large-500": ("os dois modelos large em error_500 (mini em normal)",
                  [{"provider": "openai", "model": "gpt-fake-large", "mode": "error_500"},
                   {"provider": "anthropic", "model": "claude-fake-large", "mode": "error_500"}],
                  ["F1", "F4", "F2", "F3"]),
    "timeout-f1": ("primário da F1 (gpt-fake-large) em timeout",
                   [{"provider": "openai", "model": "gpt-fake-large", "mode": "timeout"}], ["F1"]),
    "rate-limit-f1": ("openai inteiro em error_429 (Retry-After: 2)",
                      [{"provider": "openai", "mode": "error_429"}], ["F1"]),
    "all-down": ("os dois providers em error_500", [{"provider": "openai", "mode": "error_500"},
                 {"provider": "anthropic", "mode": "error_500"}], ["F1", "F4", "F2", "F3"]),
}


def main(names: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name in names or SCENARIOS:
        title, modes, features = SCENARIOS[name]
        lines = [f"## {name}: {title}", f"modos: {json.dumps(modes, ensure_ascii=False)}"]
        for feature in features:
            fail(*modes)  # reset + modos: cada feature começa com o registro limpo
            run(feature, lines)
        http("POST", f"{FAKE}/admin/reset")
        text = "\n".join(lines) + "\n"
        (OUT / f"{name}.txt").write_text(text, encoding="utf-8")
        print(text)


if __name__ == "__main__":
    main(sys.argv[1:])
