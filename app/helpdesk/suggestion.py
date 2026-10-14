from .ports import LLMGateway
from .schemas import ReplySuggestion, TicketInput

CAPABILITY = "ticket-reply"


def suggest(ticket: TicketInput, gateway: LLMGateway) -> ReplySuggestion:
    text = gateway.complete(CAPABILITY, f"TASK: suggest\n{ticket.text}")
    return ReplySuggestion(ticket_id=ticket.ticket_id, suggestion=text)
