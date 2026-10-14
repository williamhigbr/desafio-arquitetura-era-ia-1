"""Adapter do AI Gateway: o único componente da app que fala com o gateway.

O LiteLLM expõe uma API compatível com Chat Completions, então basta o SDK `openai` apontado
para ele. Retry fica desligado aqui (`max_retries=0`): o dono do retry e do fallback é o gateway,
e repetir nos dois lugares multiplica as chamadas.
"""
from collections.abc import Iterator

import openai
from openai import OpenAI

from ..ports import LLMGateway


class GatewayUnavailable(Exception):
    """O gateway não entregou resposta: todos os destinos da capacidade falharam, timeout ou recusa."""

    def __init__(self, capability: str, reason: str):
        super().__init__(f"{capability}: {reason}")
        self.capability = capability
        self.reason = reason


class StreamInterrupted(Exception):
    """O stream começou e foi interrompido antes do fim (o status 200 já foi enviado ao cliente)."""


class GatewayClient(LLMGateway):
    def __init__(self, base_url: str, api_key: str, timeout: float):
        # Sem streaming, `timeout` limita a chamada inteira; em streaming, limita cada leitura
        # (um trecho que demore mais que isso interrompe o stream).
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

    def stream(self, capability: str, prompt: str) -> Iterator[str]:
        # Abre o stream já aqui (não dentro do gerador): o gateway só responde depois do primeiro
        # trecho ou depois de esgotar os destinos, então uma falha total vira GatewayUnavailable
        # antes de a app enviar o status ao cliente.
        try:
            response = self._client.chat.completions.create(
                model=capability,
                messages=[{"role": "user", "content": prompt}],
                stream=True,
            )
        except openai.APIStatusError as err:
            raise GatewayUnavailable(capability, f"gateway respondeu {err.status_code}") from err
        except openai.APIError as err:
            raise GatewayUnavailable(capability, type(err).__name__) from err
        return self._chunks(response)

    @staticmethod
    def _chunks(response) -> Iterator[str]:
        try:
            for event in response:
                if event.choices and event.choices[0].delta.content:
                    yield event.choices[0].delta.content
        except (openai.APIError, ValueError) as err:  # erro SSE do gateway, conexão cortada, JSON inválido
            raise StreamInterrupted(str(err)) from err
        finally:
            response.close()
