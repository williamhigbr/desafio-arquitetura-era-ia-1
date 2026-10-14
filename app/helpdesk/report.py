import json
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from fastapi import HTTPException

from .ports import JobStore, LLMGateway, TicketSource
from .schemas import PeriodInput, Topic, TopicsReport

CAPABILITY = "topics-report"
# O mês inteiro (~222 mil tokens) não cabe nos 8.000 de contexto: 150 tickets por chamada.
TICKETS_PER_CALL = 150
# Lotes em paralelo: 34 lotes de ~9,8 s levariam ~5,5 min em série; com 4, ~1,5 min.
# Limite baixo de propósito, para não transformar um relatório em rajada contra o provider.
PARALLEL_BATCHES = 4
# Prazo máximo do trabalho: acima disso ele aparece como `failed`, nunca preso em `running`.
JOB_DEADLINE_SECONDS = 600


def validate(period: PeriodInput) -> None:
    if period.start > period.end:
        raise HTTPException(status_code=422, detail="A data inicial é posterior à data final")


def start(period: PeriodInput, tickets: TicketSource, jobs: JobStore) -> tuple[str, list[dict]]:
    """Registra o trabalho e devolve (id, tickets do período) para quem vai executá-lo."""
    validate(period)
    selected = tickets.in_period(period.start, period.end)
    return jobs.create(total=len(selected), deadline_seconds=JOB_DEADLINE_SECONDS), selected


def run(job_id: str, period: PeriodInput, selected: list[dict], gateway: LLMGateway, jobs: JobStore) -> None:
    """Executa o relatório em segundo plano e registra o resultado ou o motivo da falha."""
    try:
        report = topics(period, selected, gateway, on_progress=lambda n: jobs.progress(job_id, n))
    except Exception as err:  # qualquer falha de lote (já depois dos fallbacks do gateway) encerra o trabalho
        jobs.fail(job_id, f"Serviço de IA indisponível ao processar um lote de tickets ({err})")
    else:
        jobs.finish(job_id, report)


def topics(period: PeriodInput, selected: list[dict], gateway: LLMGateway, on_progress=None) -> TopicsReport:
    batches = [selected[i:i + TICKETS_PER_CALL] for i in range(0, len(selected), TICKETS_PER_CALL)]
    pool = ThreadPoolExecutor(max_workers=PARALLEL_BATCHES)
    futures = [pool.submit(_batch_topics, batch, gateway) for batch in batches]
    processed = 0
    pending = set(futures)
    try:
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                future.result()  # propaga a primeira falha
                processed += len(batches[futures.index(future)])
            if on_progress:
                on_progress(processed)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)  # numa falha, lotes ainda não iniciados não rodam

    # Agrega na ordem dos lotes (não na de conclusão), igual à versão em série.
    totals: dict[str, dict] = {}
    for future in futures:
        for item in future.result():
            current = totals.setdefault(item["topic"], {"count": 0, "examples": []})
            current["count"] += item["count"]
            current["examples"] = (current["examples"] + item["examples"])[:3]

    items = [Topic(topic=name, **data) for name, data in totals.items()]
    items.sort(key=lambda t: (-t.count, t.topic))
    return TopicsReport(start=period.start, end=period.end, total_tickets=len(selected), topics=items)


def _batch_topics(batch: list[dict], gateway: LLMGateway) -> list[dict]:
    content = "\n".join(f"[{t['id']}] {t['text']}" for t in batch)
    return json.loads(gateway.complete(CAPABILITY, f"TASK: topics\n{content}"))["topics"]
