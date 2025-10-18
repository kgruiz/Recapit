from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..core.types import Job, Kind, PdfMode, Asset
from ..core.contracts import Ingestor, Normalizer


@dataclass
class PlanReport:
    job: Job
    assets: list[Asset]
    normalized: list[Asset]
    kind: Kind
    modality: str | None
    chunks: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "job": {
                "source": self.job.source,
                "recursive": self.job.recursive,
                "kind": self.job.kind.value if self.job.kind else None,
                "pdf_mode": self.job.pdf_mode.value,
                "model": self.job.model,
                "preset": self.job.preset,
                "export": list(self.job.export or []),
                "skip_existing": self.job.skip_existing,
            },
            "kind": self.kind.value,
            "modality": self.modality,
            "assets": [self._asset_to_dict(a) for a in self.assets],
            "normalized": [self._asset_to_dict(a) for a in self.normalized],
            "chunks": list(self.chunks),
        }

    @staticmethod
    def _asset_to_dict(asset: Asset) -> dict[str, Any]:
        return {
            "path": str(asset.path),
            "media": asset.media,
            "page_index": asset.page_index,
            "source_kind": asset.source_kind.value,
            "mime": asset.mime,
            "meta": dict(asset.meta or {}),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


class Planner:
    """Lightweight helper for the CLI `plan` command."""

    def __init__(self, *, ingestor: Ingestor, normalizer: Normalizer) -> None:
        self._ingestor = ingestor
        self._normalizer = normalizer

    def plan(self, job: Job) -> PlanReport:
        assets = self._ingestor.discover(job)
        normalized = self._normalizer.normalize(assets, job.pdf_mode)
        kind = (job.kind or self._infer_kind(assets)) if assets else (job.kind or Kind.DOCUMENT)
        modality = self._determine_modality(normalized, job.pdf_mode)
        return PlanReport(job=job, assets=assets, normalized=normalized, kind=kind, modality=modality, chunks=[])

    @staticmethod
    def _infer_kind(assets: list[Asset]) -> Kind:
        if assets and assets[0].media == "video":
            return Kind.LECTURE
        if assets and assets[0].media == "image":
            return Kind.SLIDES
        return Kind.DOCUMENT

    @staticmethod
    def _determine_modality(assets: list[Asset], pdf_mode: PdfMode) -> str | None:
        if not assets:
            return None
        if assets[0].media in {"video", "audio"}:
            return "video"
        return "pdf" if pdf_mode == PdfMode.PDF else "image"
