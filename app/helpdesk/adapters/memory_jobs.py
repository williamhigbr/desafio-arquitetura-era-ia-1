"""Adapter de trabalhos em segundo plano: estado em memória do processo.

Suficiente com um único processo da app (o uvicorn sobe com 1 worker). O estado se perde num
restart e não é compartilhado entre réplicas; trocar por banco/fila é trocar este adapter
(ADR 0004, seção F3).
"""
import threading
import time
import uuid
from typing import Any

from ..ports import JobStore


class MemoryJobStore(JobStore):
    def __init__(self, clock=time.monotonic):
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._clock = clock

    def create(self, total: int, deadline_seconds: float) -> str:
        job_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._jobs[job_id] = {"state": "running", "processed": 0, "total": total, "result": None,
                                  "reason": None, "deadline": self._clock() + deadline_seconds}
        return job_id

    def progress(self, job_id: str, processed: int) -> None:
        self._update(job_id, processed=processed)

    def finish(self, job_id: str, result: Any) -> None:
        self._update(job_id, state="done", result=result)

    def fail(self, job_id: str, reason: str) -> None:
        self._update(job_id, state="failed", reason=reason)

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job["state"] == "running" and self._clock() > job["deadline"]:
                job.update(state="failed", reason="Prazo máximo de processamento excedido")
            return {k: v for k, v in job.items() if k != "deadline"}

    def _update(self, job_id: str, **fields) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if job["state"] == "running":  # estado final não muda (ex.: trabalho que já venceu o prazo)
                job.update(fields)
