"""Contrato da `main`, medido pela borda, um teste por critério de aceite dos fluxos.

Os valores esperados de conteúdo reaproveitam os golden masters da caracterização: mudou o modo
de entrega (stream, assíncrono), não o conteúdo.
"""
import time

import pytest
from conftest import TICKET_TK42, read_sse, wait_report

LARGE_DOWN = [{"provider": "openai", "model": "gpt-fake-large", "mode": "error_500"},
              {"provider": "anthropic", "model": "claude-fake-large", "mode": "error_500"}]
ALL_DOWN = [{"provider": "openai", "mode": "error_500"}, {"provider": "anthropic", "mode": "error_500"}]
UNAVAILABLE = "Serviço de IA indisponível no momento. Tente novamente mais tarde."


# --- F1: síncrono --------------------------------------------------------------------------

def test_f1_sincrona_em_ate_3s(client):
    started = time.monotonic()
    response = client.post("/tickets/classification", json=TICKET_TK42)
    assert time.monotonic() - started <= 3
    assert response.status_code == 200
    assert response.json() == {"ticket_id": "TK-00042", "category": "delivery", "priority": "high"}


def test_f1_primario_em_timeout_responde_em_ate_15s(client, provider):
    provider.fail({"provider": "openai", "model": "gpt-fake-large", "mode": "timeout"})
    started = time.monotonic()
    response = client.post("/tickets/classification", json=TICKET_TK42)
    assert time.monotonic() - started <= 15
    assert response.status_code == 200
    assert response.json()["category"] == "delivery"


def test_f1_primario_em_500_usa_o_outro_provider(client, provider):
    provider.fail({"provider": "openai", "mode": "error_500"})
    assert client.post("/tickets/classification", json=TICKET_TK42).status_code == 200
    calls = [(c["model"], c["status"]) for c in provider.calls("classify")]
    assert calls == [("gpt-fake-large", 500), ("gpt-fake-large", 500), ("claude-fake-large", 200)]


def test_f1_sem_large_responde_com_mini(client, provider):
    provider.fail(*LARGE_DOWN)
    response = client.post("/tickets/classification", json=TICKET_TK42)
    assert response.status_code == 200
    assert response.json() == {"ticket_id": "TK-00042", "category": "delivery", "priority": "high"}
    assert provider.calls("classify")[-1]["model"] == "gpt-fake-mini"


# --- F4: síncrono, sem modelo fraco --------------------------------------------------------

def test_f4_sincrona(client):
    response = client.post("/tickets/extraction", json=TICKET_TK42)
    assert response.status_code == 200
    assert response.json() == {"ticket_id": "TK-00042", "order_number": "#481516", "product": None}


def test_f4_sem_large_falha_explicitamente(client, provider):
    provider.fail(*LARGE_DOWN)
    response = client.post("/tickets/extraction", json=TICKET_TK42)
    assert response.status_code == 503
    assert response.headers["retry-after"] == "30"
    assert response.json() == {"detail": UNAVAILABLE, "capability": "ticket-extract"}
    assert all(c["model"].endswith("-large") for c in provider.calls("extract"))  # nunca o mini


# --- F2: streaming SSE ---------------------------------------------------------------------

def test_f2_stream_primeiro_trecho_em_ate_1_5s_e_texto_completo(client, golden):
    status, headers, events, first, total = read_sse(client, TICKET_TK42)
    assert status == 200
    assert headers["content-type"].startswith("text/event-stream")
    assert first <= 1.5 and first < total / 2
    chunks = [data["chunk"] for name, data in events if name == "message"]
    assert len(chunks) > 1
    assert "".join(chunks) == golden("suggestion_TK-00042.json")["suggestion"]
    assert events[-1] == ("end", {"ticket_id": "TK-00042"})


def test_f2_erro_no_meio_do_stream_vira_evento_error(client, provider):
    provider.fail({"provider": "anthropic", "mode": "midstream_error"})
    status, _, events, first, _ = read_sse(client, TICKET_TK42)
    assert status == 200 and first is not None
    assert events[-1] == ("error", {"ticket_id": "TK-00042", "message": "A geração foi interrompida"})
    assert not any(name == "end" for name, _ in events)


def test_f2_primario_em_timeout_troca_de_destino_e_completa(client, provider, golden):
    provider.fail({"provider": "anthropic", "model": "claude-fake-large", "mode": "timeout"})
    status, _, events, first, total = read_sse(client, TICKET_TK42)
    assert status == 200 and first < 10
    assert "".join(d["chunk"] for n, d in events if n == "message") == golden("suggestion_TK-00042.json")["suggestion"]
    assert events[-1][0] == "end"


def test_f2_sem_large_responde_com_mini(client, provider):
    provider.fail(*LARGE_DOWN)
    status, _, events, _, _ = read_sse(client, TICKET_TK42)
    assert status == 200 and events[-1][0] == "end"
    assert provider.calls("suggest")[-1]["model"] == "claude-fake-mini"


def test_f2_sem_nenhum_modelo_falha_antes_do_stream(client, provider):
    provider.fail(*ALL_DOWN)
    status, headers, body, _, _ = read_sse(client, TICKET_TK42)
    assert status == 503
    assert body == {"detail": UNAVAILABLE, "capability": "ticket-reply"}


def test_f2_entrada_invalida(client):
    response = client.post("/tickets/reply-suggestion", json={"ticket_id": "TK-00042", "text": ""})
    assert response.status_code == 422


# --- F3: assíncrono (Async Request-Reply) --------------------------------------------------

def test_f3_mes_inteiro_aceito_em_ate_1s_e_relatorio_completo_depois(client, provider):
    started = time.monotonic()
    response = client.post("/reports/topics", json={"start": "2026-08-01", "end": "2026-08-31"})
    assert time.monotonic() - started <= 1
    assert response.status_code == 202
    status_url = response.headers["location"]
    assert status_url == f"/reports/topics/status/{response.json()['id']}"
    assert response.headers["retry-after"] == "5"

    running = client.get(status_url).json()
    assert running["state"] == "running" and running["progress"].endswith("/5000")

    final, _ = wait_report(client, status_url, limit_seconds=300)
    assert final.status_code == 303
    result = client.get(final.headers["location"])
    assert result.status_code == 200
    report = result.json()
    assert (report["start"], report["end"], report["total_tickets"]) == ("2026-08-01", "2026-08-31", 5000)
    assert sum(t["count"] for t in report["topics"]) == 5000
    assert len(provider.calls("topics")) == 34  # 5.000 tickets em lotes de 150


def test_f3_um_dia_igual_ao_golden(client, golden):
    response = client.post("/reports/topics", json={"start": "2026-08-01", "end": "2026-08-01"})
    final, _ = wait_report(client, response.headers["location"], limit_seconds=60)
    assert final.status_code == 303
    assert client.get(final.headers["location"]).json() == golden("topics_2026-08-01.json")


def test_f3_sem_providers_falha_visivel_em_ate_60s(client, provider):
    provider.fail(*ALL_DOWN)
    response = client.post("/reports/topics", json={"start": "2026-08-01", "end": "2026-08-31"})
    assert response.status_code == 202
    final, seconds = wait_report(client, response.headers["location"], limit_seconds=60)
    assert seconds <= 60
    body = final.json()
    assert final.status_code == 200 and body["state"] == "failed"
    assert body["reason"].startswith("Serviço de IA indisponível")


def test_f3_resultado_antes_de_pronto_e_id_desconhecido(client):
    response = client.post("/reports/topics", json={"start": "2026-08-01", "end": "2026-08-01"})
    assert client.get(f"/reports/topics/{response.json()['id']}").status_code == 404  # ainda running
    assert client.get("/reports/topics/status/nao-existe").status_code == 404
    wait_report(client, response.headers["location"], limit_seconds=60)


@pytest.mark.parametrize("period, detail", [
    ({"start": "2026-08-02", "end": "2026-08-01"}, "A data inicial é posterior à data final"),
])
def test_f3_periodo_invalido_recusado_na_hora(client, period, detail):
    response = client.post("/reports/topics", json=period)
    assert response.status_code == 422
    assert response.json() == {"detail": detail}
