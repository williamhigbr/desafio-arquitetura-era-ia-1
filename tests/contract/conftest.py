"""Fixtures da suíte de contrato da `main` (modos de execução finais), pela borda.

`tests/characterization/` continua igual ao das tags `v1-coupled` e `v2-decoupled` (é o juiz da
refatoração). Esta suíte fixa o contrato da `main`: F1 e F4 síncronas, F2 em streaming SSE e F3
assíncrona (Async Request-Reply), mais os cenários de falha dos critérios de aceite.
"""
import json
import os
import time
from pathlib import Path

import httpx
import pytest

HELPDESK_URL = os.environ.get("HELPDESK_URL", "http://localhost:8000")
PROVIDER_URL = os.environ.get("PROVIDER_URL", "http://localhost:8090")
GOLDEN_DIR = Path(__file__).resolve().parent.parent / "characterization" / "golden"
TICKET_TK42 = {"ticket_id": "TK-00042", "text": "Meu pedido #481516 não chegou e já passou do prazo, urgente"}


@pytest.fixture(autouse=True)
def reset_provider():
    httpx.post(f"{PROVIDER_URL}/admin/reset", timeout=5.0).raise_for_status()
    yield
    httpx.post(f"{PROVIDER_URL}/admin/reset", timeout=5.0).raise_for_status()


@pytest.fixture
def provider():
    """provider(modo, ...) aplica modos de falha; calls(task) lista as chamadas da tarefa (mais antiga primeiro)."""
    class Provider:
        def fail(self, *modes):
            for mode in modes:
                httpx.post(f"{PROVIDER_URL}/admin/failures", json=mode, timeout=5.0).raise_for_status()

        def calls(self, task):
            calls = httpx.get(f"{PROVIDER_URL}/admin/calls", params={"last": 5000}, timeout=5.0).json()
            return [c for c in reversed(calls) if c["task"] == task]
    return Provider()


@pytest.fixture(scope="session")
def client():
    # follow_redirects=False: o 303 da F3 faz parte do contrato.
    with httpx.Client(base_url=HELPDESK_URL, timeout=httpx.Timeout(40.0, connect=5.0)) as c:
        yield c


@pytest.fixture(scope="session")
def golden():
    return lambda name: json.loads((GOLDEN_DIR / name).read_text(encoding="utf-8"))


def read_sse(client, ticket):
    """Consome a F2 e devolve (status, headers, eventos, segundos até o 1º trecho, segundos totais).

    Cada evento é (nome, dados); `message` é o nome padrão do SSE para linhas `data:` sem `event:`.
    """
    started = time.monotonic()
    first = None
    events = []
    with client.stream("POST", "/tickets/reply-suggestion", json=ticket) as response:
        if response.status_code != 200:
            response.read()
            return response.status_code, response.headers, response.json(), None, time.monotonic() - started
        name = "message"
        for line in response.iter_lines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
                if name == "message" and first is None:
                    first = time.monotonic() - started
                events.append((name, data))
                name = "message"
        return response.status_code, response.headers, events, first, time.monotonic() - started


def wait_report(client, status_url, limit_seconds):
    """Faz polling do status até sair de `running`; devolve (resposta final, segundos)."""
    started = time.monotonic()
    while True:
        response = client.get(status_url)
        if response.status_code != 200 or response.json()["state"] != "running":
            return response, time.monotonic() - started
        assert time.monotonic() - started < limit_seconds, f"ainda running após {limit_seconds} s"
        time.sleep(1)
