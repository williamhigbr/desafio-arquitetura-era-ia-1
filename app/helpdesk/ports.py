"""Portas da aplicação: o que as features precisam do mundo externo, sem dizer como é feito."""
from collections.abc import Iterator
from datetime import date
from typing import Any, Protocol


class LLMGateway(Protocol):
    """Usa uma capacidade lógica do AI Gateway (ex.: `ticket-classify`).

    Quem implementa decide transporte, credencial e timeout; a feature conhece só o nome da
    capacidade e o texto. Erros de infraestrutura sobem como exceção.
    """

    def complete(self, capability: str, prompt: str) -> str:
        """Resposta completa, de uma vez."""
        ...

    def stream(self, capability: str, prompt: str) -> Iterator[str]:
        """Resposta em trechos, à medida que é gerada.

        Falha ao abrir (nenhum destino respondeu) sobe já na chamada; falha depois do primeiro
        trecho sobe durante a iteração.
        """
        ...


class TicketSource(Protocol):
    """Tickets criados no período [start, end], na ordem de origem, como dicts com `id` e `text`."""

    def in_period(self, start: date, end: date) -> list[dict]: ...


class JobStore(Protocol):
    """Estado de trabalhos em segundo plano (F3), consultável por id.

    Estados: `running` → `done` (com resultado) ou `failed` (com motivo). Um trabalho que passa do
    prazo sem terminar é visto como `failed`, nunca fica preso em `running`.
    """

    def create(self, total: int, deadline_seconds: float) -> str: ...

    def progress(self, job_id: str, processed: int) -> None: ...

    def finish(self, job_id: str, result: Any) -> None: ...

    def fail(self, job_id: str, reason: str) -> None: ...

    def get(self, job_id: str) -> dict | None:
        """{"state", "processed", "total", "result", "reason"} ou None se o id não existe."""
        ...
