import json

from fastapi import HTTPException

from .ports import LLMGateway
from .schemas import Classification, TicketInput

CAPABILITY = "ticket-classify"
CATEGORIES = {"delivery", "payment", "exchange_return", "product_defect", "other"}
PRIORITIES = {"low", "medium", "high"}


def classify(ticket: TicketInput, gateway: LLMGateway) -> Classification:
    text = gateway.complete(CAPABILITY, f"TASK: classify\n{ticket.text}")
    data = json.loads(text)
    if data.get("category") not in CATEGORIES or data.get("priority") not in PRIORITIES:
        raise HTTPException(status_code=502, detail="Classificação fora dos valores permitidos")
    return Classification(ticket_id=ticket.ticket_id, **data)
