import os

from fastapi import FastAPI

from . import classification, extraction, report, suggestion
from .adapters.gateway import GatewayClient
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
