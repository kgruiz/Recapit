from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Dict, Iterable, List


@dataclass(frozen=True)
class RequestEvent:
    """Single Gemini API interaction captured for monitoring."""

    model: str
    modality: str
    started_at: datetime
    finished_at: datetime
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    metadata: Dict[str, object] = field(default_factory=dict)

    @property
    def duration_seconds(self) -> float:
        return max((self.finished_at - self.started_at).total_seconds(), 0.0)

    def to_dict(self) -> Dict[str, object]:
        return {
            "model": self.model,
            "modality": self.modality,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_seconds": self.duration_seconds,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class RunSummary:
    total_requests: int
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_duration_seconds: float
    by_model: Dict[str, Dict[str, float]]
    by_modality: Dict[str, Dict[str, float]]

    def to_dict(self) -> Dict[str, object]:
        return {
            "total_requests": self.total_requests,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_tokens,
            "total_duration_seconds": self.total_duration_seconds,
            "by_model": self.by_model,
            "by_modality": self.by_modality,
        }


class RunMonitor:
    """Collects Gemini request telemetry for the lifetime of a pipeline run."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._events: List[RequestEvent] = []
        self._notes: List[Dict[str, object]] = []

    def record(self, event: RequestEvent) -> None:
        with self._lock:
            self._events.append(event)

    def note_event(self, name: str, payload: Dict[str, object] | None = None) -> None:
        with self._lock:
            self._notes.append(
                {
                    "name": name,
                    "payload": dict(payload or {}),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

    def events(self) -> List[RequestEvent]:
        with self._lock:
            return list(self._events)

    def notes(self) -> List[Dict[str, object]]:
        with self._lock:
            return list(self._notes)

    def summarize(self) -> RunSummary:
        events = self.events()
        total_requests = len(events)
        total_input_tokens = sum(e.input_tokens or 0 for e in events)
        total_output_tokens = sum(e.output_tokens or 0 for e in events)
        total_tokens = sum(e.total_tokens or 0 for e in events)
        total_duration = sum(e.duration_seconds for e in events)

        by_model: Dict[str, Dict[str, float]] = {}
        by_modality: Dict[str, Dict[str, float]] = {}

        def _update(bucket: Dict[str, Dict[str, float]], key: str, event: RequestEvent) -> None:
            stats = bucket.setdefault(
                key,
                {
                    "requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "total_duration_seconds": 0.0,
                },
            )
            stats["requests"] += 1
            stats["input_tokens"] += event.input_tokens or 0
            stats["output_tokens"] += event.output_tokens or 0
            stats["total_tokens"] += event.total_tokens or 0
            stats["total_duration_seconds"] += event.duration_seconds

        for ev in events:
            _update(by_model, ev.model, ev)
            _update(by_modality, ev.modality, ev)

        return RunSummary(
            total_requests=total_requests,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            total_tokens=total_tokens,
            total_duration_seconds=total_duration,
            by_model=by_model,
            by_modality=by_modality,
        )

    def costs(self) -> "CostSummary":
        from .costs import estimate_costs

        return estimate_costs(self.events())

    def flush_summary(self, *, to: Path, cost: "CostEstimator") -> None:
        summary = self.summarize()
        costs = cost.estimate(self.events())
        payload = {
            "summary": summary.to_dict(),
            "costs": costs.to_dict(),
            "notes": self.notes(),
        }
        to.parent.mkdir(parents=True, exist_ok=True)
        to.write_text(json.dumps(payload, indent=2, sort_keys=True))
