"""Portas da aplicação: o que as features precisam do mundo externo, sem dizer como é feito."""
from datetime import date
from typing import Protocol


class LLMGateway(Protocol):
    """Completa um prompt usando uma capacidade lógica do AI Gateway (ex.: `ticket-classify`).

    Quem implementa decide transporte, credencial e timeout; a feature conhece só o nome da
    capacidade e o texto. Erros de infraestrutura sobem como exceção.
    """

    def complete(self, capability: str, prompt: str) -> str: ...


class TicketSource(Protocol):
    """Tickets criados no período [start, end], na ordem de origem, como dicts com `id` e `text`."""

    def in_period(self, start: date, end: date) -> list[dict]: ...
