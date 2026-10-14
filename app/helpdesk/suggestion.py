from collections.abc import Iterator

from .ports import LLMGateway
from .schemas import TicketInput

CAPABILITY = "ticket-reply"


def suggest_stream(ticket: TicketInput, gateway: LLMGateway) -> Iterator[str]:
    """Trechos da sugestão à medida que o modelo gera; o atendente lê enquanto o texto chega."""
    return gateway.stream(CAPABILITY, f"TASK: suggest\n{ticket.text}")
