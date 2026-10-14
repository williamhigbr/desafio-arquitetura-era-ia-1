import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import classification, extraction, report, suggestion
from .adapters.gateway import GatewayClient, GatewayUnavailable
from .adapters.tickets_file import JsonlTicketSource
from .schemas import (Classification, OrderData, PeriodInput, ReplySuggestion, TicketInput,
                      TopicsReport)

# Ponto de composição: o único módulo que lê o ambiente e escolhe as implementações das portas.
# A app só conhece o endereço e a chave do gateway; modelos, providers e credenciais ficam nele.
GATEWAY_URL = os.environ["GATEWAY_URL"]
GATEWAY_API_KEY = os.environ["GATEWAY_API_KEY"]
# Abaixo dos 30 s da borda: a app não fica esperando por uma requisição que a borda já abandonou.
GATEWAY_TIMEOUT = 25.0
TICKETS_FILE = "/data/tickets.jsonl"   # volume ./data montado no compose

gateway = GatewayClient(base_url=GATEWAY_URL, api_key=GATEWAY_API_KEY, timeout=GATEWAY_TIMEOUT)
tickets = JsonlTicketSource(TICKETS_FILE)

app = FastAPI(title="Helpdesk")


@app.exception_handler(GatewayUnavailable)
def gateway_unavailable(request: Request, exc: GatewayUnavailable) -> JSONResponse:
    """Nenhum destino da capacidade respondeu (primário, fallback técnico e, se houver, modelo fraco).

    Falha explícita: 503, em vez do 500 genérico da v1. Para F4, que não tem modelo fraco por decisão
    de produto, é a resposta esperada quando os dois modelos large estão fora.
    """
    return JSONResponse(status_code=503, headers={"Retry-After": "30"}, content={
        "detail": "Serviço de IA indisponível no momento. Tente novamente mais tarde.",
        "capability": exc.capability,
    })


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/tickets/classification")
def classify_ticket(ticket: TicketInput) -> Classification:
    return classification.classify(ticket, gateway)


@app.post("/tickets/reply-suggestion")
def suggest_reply(ticket: TicketInput) -> ReplySuggestion:
    return suggestion.suggest(ticket, gateway)


@app.post("/tickets/extraction")
def extract_order_data(ticket: TicketInput) -> OrderData:
    return extraction.extract(ticket, gateway)


@app.post("/reports/topics")
def topics_report(period: PeriodInput) -> TopicsReport:
    return report.topics(period, gateway, tickets)
