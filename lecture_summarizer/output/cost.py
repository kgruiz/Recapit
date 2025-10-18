from __future__ import annotations

from typing import Iterable

from ..costs import estimate_costs, CostSummary
from ..telemetry import RequestEvent


class CostEstimator:
    """Thin wrapper around the existing cost estimation utilities."""

    def estimate(self, events: Iterable[RequestEvent]) -> CostSummary:
        return estimate_costs(events)
