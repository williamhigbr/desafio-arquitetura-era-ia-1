"""Caracterização do helpdesk v1 pela borda (golden master).

Os valores esperados foram capturados da aplicação recebida (`app/` idêntico ao base),
com o provider-fake em modo normal; nenhum foi inventado. O que fica fixado é o contrato HTTP:
status e corpo completo. Detalhes que mudam legitimamente numa refatoração (quantas chamadas
chegam ao provider, qual provider atende, tempos) ficam de fora de propósito.
"""
import pytest

TICKET_TK42 = {"ticket_id": "TK-00042", "text": "Meu pedido #481516 não chegou e já passou do prazo, urgente"}
# Ticket real do dataset com nota fiscal e pedido no mesmo texto, e produto nomeado.
TICKET_TK36 = {
    "ticket_id": "TK-00036",
    "text": "Tenho aqui a nota fiscal 927477. Recebi o tapete de yoga e o aparelho não liga, "
            "pedido nº 389511. É só uma dúvida. Prefiro contato por e-mail. Podem me ajudar, por favor?",
}

TICKET_ROUTES = ["/tickets/classification", "/tickets/reply-suggestion", "/tickets/extraction"]


def assert_validation_error(response, field: str, error_type: str):
    """422 do FastAPI apontando o campo e o tipo de erro do pydantic."""
    assert response.status_code == 422
    errors = [(e["loc"], e["type"]) for e in response.json()["detail"]]
    assert errors == [(["body", field], error_type)]


# --- F1: classificação ------------------------------------------------------------------

@pytest.mark.parametrize("ticket, expected", [
    (TICKET_TK42, {"ticket_id": "TK-00042", "category": "delivery", "priority": "high"}),
    (TICKET_TK36, {"ticket_id": "TK-00036", "category": "product_defect", "priority": "low"}),
])
def test_f1_classifica_ticket(client, ticket, expected):
    response = client.post("/tickets/classification", json=ticket)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == expected


# --- F4: extração -----------------------------------------------------------------------

@pytest.mark.parametrize("ticket, expected", [
    (TICKET_TK42, {"ticket_id": "TK-00042", "order_number": "#481516", "product": None}),
    # o número da nota fiscal (927477) não pode ser confundido com o do pedido
    (TICKET_TK36, {"ticket_id": "TK-00036", "order_number": "#389511", "product": "tapete de yoga"}),
])
def test_f4_extrai_dados_do_pedido(client, ticket, expected):
    response = client.post("/tickets/extraction", json=ticket)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == expected


# --- F2: sugestão de resposta -----------------------------------------------------------

def test_f2_sugere_resposta(client, golden):
    response = client.post("/tickets/reply-suggestion", json=TICKET_TK42)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    expected = golden("suggestion_TK-00042.json")
    body = response.json()
    assert body["ticket_id"] == expected["ticket_id"]
    # texto exato: a saída do modelo é determinística (mensagem de falha mais legível com o tamanho)
    assert len(body["suggestion"]) == len(expected["suggestion"])
    assert body == expected


# --- Erros de entrada comuns a F1, F2 e F4 ----------------------------------------------

@pytest.mark.parametrize("route", TICKET_ROUTES)
def test_ticket_sem_texto_e_recusado(client, route):
    response = client.post(route, json={"ticket_id": "TK-00042"})
    assert_validation_error(response, "text", "missing")


@pytest.mark.parametrize("route", TICKET_ROUTES)
def test_ticket_com_texto_vazio_e_recusado(client, route):
    response = client.post(route, json={"ticket_id": "TK-00042", "text": ""})
    assert_validation_error(response, "text", "string_too_short")


@pytest.mark.parametrize("route", TICKET_ROUTES)
def test_ticket_sem_id_e_recusado(client, route):
    response = client.post(route, json={"text": "Meu pedido não chegou"})
    assert_validation_error(response, "ticket_id", "missing")


# --- F3: relatório de temas -------------------------------------------------------------
# Período de 1 dia (137 tickets, 1 lote): cabe abaixo dos 30 s da borda ainda na v1.

def test_f3_relatorio_de_um_dia(client, golden):
    response = client.post("/reports/topics", json={"start": "2026-08-01", "end": "2026-08-01"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == golden("topics_2026-08-01.json")


def test_f3_periodo_sem_tickets(client):
    response = client.post("/reports/topics", json={"start": "2027-01-01", "end": "2027-01-02"})
    assert response.status_code == 200
    assert response.json() == {"start": "2027-01-01", "end": "2027-01-02", "total_tickets": 0, "topics": []}


def test_f3_inicio_depois_do_fim_e_recusado(client):
    response = client.post("/reports/topics", json={"start": "2026-08-02", "end": "2026-08-01"})
    assert response.status_code == 422
    assert response.json() == {"detail": "A data inicial é posterior à data final"}


def test_f3_sem_data_final_e_recusado(client):
    response = client.post("/reports/topics", json={"start": "2026-08-01"})
    assert_validation_error(response, "end", "missing")


def test_f3_data_invalida_e_recusada(client):
    response = client.post("/reports/topics", json={"start": "2026-08-01", "end": "ontem"})
    assert response.status_code == 422
    assert [e["loc"] for e in response.json()["detail"]] == [["body", "end"]]
