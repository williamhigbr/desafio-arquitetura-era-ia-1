"""Fixtures da suíte de caracterização.

A suíte fala só HTTP com a borda (contrato público do helpdesk) e usa o provider-fake apenas
para voltar ao estado normal antes de cada teste. Nada aqui depende de como a app é feita.

Variáveis de ambiente:
    HELPDESK_URL   base da borda (padrão http://localhost:8000)
    PROVIDER_URL   base do provider-fake (padrão http://localhost:8090)
"""
import json
import os
from pathlib import Path

import httpx
import pytest

HELPDESK_URL = os.environ.get("HELPDESK_URL", "http://localhost:8000")
PROVIDER_URL = os.environ.get("PROVIDER_URL", "http://localhost:8090")
GOLDEN_DIR = Path(__file__).parent / "golden"

# Abaixo dos 30 s da borda: se a resposta não vier antes disso, a borda já teria dado 504.
TIMEOUT = httpx.Timeout(40.0, connect=5.0)


@pytest.fixture(autouse=True)
def reset_provider():
    """Todos os modos em `normal` e registro de chamadas limpo antes de cada teste."""
    httpx.post(f"{PROVIDER_URL}/admin/reset", timeout=5.0).raise_for_status()


@pytest.fixture(scope="session")
def client():
    with httpx.Client(base_url=HELPDESK_URL, timeout=TIMEOUT) as c:
        yield c


@pytest.fixture(scope="session")
def golden():
    def load(name: str):
        return json.loads((GOLDEN_DIR / name).read_text(encoding="utf-8"))
    return load
