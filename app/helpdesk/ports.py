"""Portas da aplicação: o que as features precisam do mundo externo, sem dizer como é feito."""
from typing import Protocol


class LLMGateway(Protocol):
    """Completa um prompt usando uma capacidade lógica do AI Gateway (ex.: `ticket-classify`).

    Quem implementa decide transporte, credencial e timeout; a feature conhece só o nome da
    capacidade e o texto. Erros de infraestrutura sobem como exceção.
    """

    def complete(self, capability: str, prompt: str) -> str: ...
