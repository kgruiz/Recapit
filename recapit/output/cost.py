from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Iterable

import yaml

from ..costs import estimate_costs, CostSummary
from ..telemetry import RequestEvent


class CostEstimator:
    """Thin wrapper around the existing cost estimation utilities."""

    def __init__(self, *, pricing: dict | None = None, pricing_path: Path | None = None) -> None:
        if pricing is not None:
            self._pricing = pricing
        else:
            self._pricing = self._load_pricing(pricing_path)

    def estimate(self, events: Iterable[RequestEvent]) -> CostSummary:
        return estimate_costs(events, pricing=self._pricing)

    def _load_pricing(self, path: Path | None) -> dict:
        data: dict | None = None
        if path is not None:
            if path.exists():
                data = self._read_yaml(path)
        else:
            try:
                with resources.files("recapit").joinpath("pricing.yaml").open("r", encoding="utf-8") as handle:
                    data = yaml.safe_load(handle)
            except FileNotFoundError:
                data = None
        if not isinstance(data, dict):
            from ..constants import MODEL_PRICING

            return MODEL_PRICING
        return data

    @staticmethod
    def _read_yaml(path: Path) -> dict | None:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
