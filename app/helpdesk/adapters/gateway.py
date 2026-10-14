"""Adapter do AI Gateway: o único componente da app que fala com o gateway.

O LiteLLM expõe uma API compatível com Chat Completions, então basta o SDK `openai` apontado
para ele. Retry fica desligado aqui (`max_retries=0`): o dono do retry é o gateway, e repetir
nos dois lugares multiplica as chamadas.
"""
from openai import OpenAI

from ..ports import LLMGateway


class GatewayClient(LLMGateway):
    def __init__(self, base_url: str, api_key: str, timeout: float):
        self._client = OpenAI(base_url=f"{base_url}/v1", api_key=api_key,
                              timeout=timeout, max_retries=0)

    def complete(self, capability: str, prompt: str) -> str:
        response = self._client.chat.completions.create(
            model=capability,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content
