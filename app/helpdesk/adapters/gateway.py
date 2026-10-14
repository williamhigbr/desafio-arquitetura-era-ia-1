"""Adapter do AI Gateway: o único componente da app que fala com o gateway.

O LiteLLM expõe uma API compatível com Chat Completions, então basta o SDK `openai` apontado
para ele. Retry fica desligado aqui (`max_retries=0`): o dono do retry e do fallback é o gateway,
e repetir nos dois lugares multiplica as chamadas.
"""
import openai
from openai import OpenAI

from ..ports import LLMGateway


class GatewayUnavailable(Exception):
    """O gateway não entregou resposta: todos os destinos da capacidade falharam, timeout ou recusa."""

    def __init__(self, capability: str, reason: str):
        super().__init__(f"{capability}: {reason}")
        self.capability = capability
        self.reason = reason


class GatewayClient(LLMGateway):
    def __init__(self, base_url: str, api_key: str, timeout: float):
        self._client = OpenAI(base_url=f"{base_url}/v1", api_key=api_key,
                              timeout=timeout, max_retries=0)

    def complete(self, capability: str, prompt: str) -> str:
        try:
            response = self._client.chat.completions.create(
                model=capability,
                messages=[{"role": "user", "content": prompt}],
            )
        except openai.APIStatusError as err:
            raise GatewayUnavailable(capability, f"gateway respondeu {err.status_code}") from err
        except openai.APIError as err:  # conexão recusada, timeout da app
            raise GatewayUnavailable(capability, type(err).__name__) from err
        return response.choices[0].message.content
