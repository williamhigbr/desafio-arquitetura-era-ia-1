import json
import os
import threading
from collections.abc import Iterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from . import classification, extraction, report, suggestion
from .adapters.gateway import GatewayClient, GatewayUnavailable, StreamInterrupted
from .adapters.memory_jobs import MemoryJobStore
from .adapters.tickets_file import JsonlTicketSource
from .schemas import Classification, OrderData, PeriodInput, TicketInput, TopicsReport

# Ponto de composição: o único módulo que lê o ambiente e escolhe as implementações das portas.
# A app só conhece o endereço e a chave do gateway; modelos, providers e credenciais ficam nele.
GATEWAY_URL = os.environ["GATEWAY_URL"]
GATEWAY_API_KEY = os.environ["GATEWAY_API_KEY"]
# Abaixo dos 30 s da borda: a app não fica esperando por uma requisição que a borda já abandonou.
GATEWAY_TIMEOUT = 25.0
TICKETS_FILE = "/data/tickets.jsonl"   # volume ./data montado no compose

gateway = GatewayClient(base_url=GATEWAY_URL, api_key=GATEWAY_API_KEY, timeout=GATEWAY_TIMEOUT)
tickets = JsonlTicketSource(TICKETS_FILE)
jobs = MemoryJobStore()

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


# F1: síncrono. Saída curta (~1 s), alguém espera agora e cabe no orçamento de 3 s.
@app.post("/tickets/classification")
def classify_ticket(ticket: TicketInput) -> Classification:
    return classification.classify(ticket, gateway)


# F2: streaming SSE. O atendente lê o texto enquanto ele é gerado (~16 s no total, 1º trecho em ~1 s).
@app.post("/tickets/reply-suggestion")
def suggest_reply(ticket: TicketInput) -> StreamingResponse:
    chunks = suggestion.suggest_stream(ticket, gateway)  # falha total aqui vira 503, antes do 200
    return StreamingResponse(
        _sse(ticket.ticket_id, chunks),
        media_type="text/event-stream",
        # A borda (nginx) acumula a resposta por padrão; este header desliga o buffer só nesta rota.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(ticket_id: str, chunks: Iterator[str]) -> Iterator[str]:
    try:
        for chunk in chunks:
            yield f"data: {json.dumps({'chunk': chunk}, ensure_ascii=False)}\n\n"
    except (StreamInterrupted, GatewayUnavailable):
        # O status 200 já foi enviado: a falha vai para dentro do stream, que termina em seguida.
        yield "event: error\n" + f"data: {json.dumps({'ticket_id': ticket_id, 'message': 'A geração foi interrompida'}, ensure_ascii=False)}\n\n"
        return
    yield "event: end\n" + f"data: {json.dumps({'ticket_id': ticket_id})}\n\n"


# F4: síncrono. Quem chama é uma automação que espera o JSON; saída curta.
@app.post("/tickets/extraction")
def extract_order_data(ticket: TicketInput) -> OrderData:
    return extraction.extract(ticket, gateway)


# F3: assíncrono (Async Request-Reply). Ninguém fica esperando o mês inteiro (34 lotes, minutos).
@app.post("/reports/topics", status_code=202)
def request_topics_report(period: PeriodInput) -> JSONResponse:
    job_id, selected = report.start(period, tickets, jobs)
    threading.Thread(target=report.run, args=(job_id, period, selected, gateway, jobs), daemon=True).start()
    status_url = f"/reports/topics/status/{job_id}"
    return JSONResponse(status_code=202, headers={"Location": status_url, "Retry-After": "5"},
                        content={"id": job_id, "state": "running", "status_url": status_url})


@app.get("/reports/topics/status/{job_id}")
def topics_report_status(job_id: str):
    job = _job_or_404(job_id)
    if isinstance(job, JSONResponse):
        return job
    if job["state"] == "done":
        return RedirectResponse(f"/reports/topics/{job_id}", status_code=303)
    body = {"state": job["state"], "progress": f"{job['processed']}/{job['total']}"}
    if job["state"] == "failed":
        body["reason"] = job["reason"]
        return JSONResponse(content=body)
    return JSONResponse(content=body, headers={"Retry-After": "5"})


@app.get("/reports/topics/{job_id}", response_model=TopicsReport)
def topics_report_result(job_id: str):
    job = _job_or_404(job_id)
    if isinstance(job, JSONResponse):
        return job
    if job["state"] != "done":
        return JSONResponse(status_code=404, content={
            "detail": "Relatório ainda não disponível", "status_url": f"/reports/topics/status/{job_id}"})
    return job["result"]


def _job_or_404(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"detail": "Relatório não encontrado"})
    return job
