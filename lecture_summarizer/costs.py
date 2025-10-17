from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable

from .constants import MODEL_PRICING
from .telemetry import RequestEvent
from .video import DEFAULT_TOKENS_PER_SECOND


_AUDIO_VIDEO_MODALITIES = {"video"}
_SKIP_MODALITIES = {"video_token_count"}


@dataclass
class CostSummary:
    total_input_cost: float = 0.0
    total_output_cost: float = 0.0
    total_cost: float = 0.0
    per_model: Dict[str, Dict[str, float]] = field(default_factory=dict)
    estimated: bool = False

    def to_dict(self) -> Dict[str, object]:
        return {
            "total_input_cost": self.total_input_cost,
            "total_output_cost": self.total_output_cost,
            "total_cost": self.total_cost,
            "per_model": self.per_model,
            "estimated": self.estimated,
        }


def estimate_costs(events: Iterable[RequestEvent]) -> CostSummary:
    summary = CostSummary()
    for event in events:
        if event.modality in _SKIP_MODALITIES:
            continue
        price_key = "audio_video" if event.modality in _AUDIO_VIDEO_MODALITIES else "text"
        pricing = MODEL_PRICING.get(event.model, MODEL_PRICING["default"]).get(price_key, MODEL_PRICING["default"][price_key])

        input_tokens = _determine_input_tokens(event)
        output_tokens = _determine_output_tokens(event)
        if input_tokens is None and output_tokens is None:
            estimated_tokens = _estimate_tokens_from_metadata(event)
            if estimated_tokens:
                output_tokens = estimated_tokens
                summary.estimated = True
            else:
                continue

        if input_tokens is None:
            input_tokens = 0
        if output_tokens is None:
            output_tokens = 0

        input_cost = (input_tokens / 1_000_000) * pricing.get("input", 0.0)
        output_cost = (output_tokens / 1_000_000) * pricing.get("output", 0.0)

        summary.total_input_cost += input_cost
        summary.total_output_cost += output_cost
        summary.total_cost += input_cost + output_cost

        model_entry = summary.per_model.setdefault(
            event.model,
            {
                "input_cost": 0.0,
                "output_cost": 0.0,
                "total_cost": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
            },
        )
        model_entry["input_cost"] += input_cost
        model_entry["output_cost"] += output_cost
        model_entry["total_cost"] += input_cost + output_cost
        model_entry["input_tokens"] += input_tokens
        model_entry["output_tokens"] += output_tokens

    return summary


def _determine_input_tokens(event: RequestEvent) -> int | None:
    if event.input_tokens is not None:
        return event.input_tokens
    if event.total_tokens is not None and event.output_tokens is not None:
        remainder = event.total_tokens - event.output_tokens
        return remainder if remainder > 0 else None
    return None


def _determine_output_tokens(event: RequestEvent) -> int | None:
    if event.output_tokens is not None:
        return event.output_tokens
    if event.total_tokens is not None and event.input_tokens is not None:
        remainder = event.total_tokens - event.input_tokens
        return remainder if remainder > 0 else None
    if event.total_tokens is not None:
        return event.total_tokens
    return None


def _estimate_tokens_from_metadata(event: RequestEvent) -> int:
    if event.modality not in _AUDIO_VIDEO_MODALITIES:
        return 0
    start = event.metadata.get("chunk_start_seconds")
    end = event.metadata.get("chunk_end_seconds")
    if start is None or end is None:
        return 0
    duration = float(end) - float(start)
    if duration <= 0:
        return 0
    return int(duration * DEFAULT_TOKENS_PER_SECOND)
