from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..core.types import Job, Kind, PdfMode, Asset
from ..core.contracts import Ingestor, Normalizer, PromptStrategy, Provider, Writer
from ..telemetry import RunMonitor
from ..output.cost import CostEstimator
from ..constants import RATE_LIMITS, TOKEN_LIMITS_PER_MINUTE


@dataclass
class Engine:
    ingestor: Ingestor
    normalizer: Normalizer
    prompts: dict[Kind, PromptStrategy]
    provider: Provider
    writer: Writer
    monitor: RunMonitor
    cost: CostEstimator

    def run(self, job: Job) -> Path | None:
        prepare = getattr(self.normalizer, "prepare", None)
        if callable(prepare):
            prepare(job)
        assets = self.ingestor.discover(job)
        if not assets:
            self.monitor.note_event("discover.empty", {"source": job.source})
            return None

        kind = job.kind or self._infer_kind(assets)
        assets = self.normalizer.normalize(assets, job.pdf_mode)
        modality = self._modality_for(assets, job.pdf_mode)
        
        strategy = self.prompts[kind]
        preamble = strategy.preamble()
        instruction = strategy.instruction(preamble)

        text = self.provider.transcribe(
            instruction=instruction,
            assets=assets,
            modality=modality,
            meta={"kind": kind.value, "source": job.source},
        )

        base_root = job.output_dir or Path(".") / "output"
        source_slug = self._slug(Path(job.source).stem if "://" not in job.source else "remote")
        base = base_root / source_slug
        name = f"{self._slug(Path(job.source).stem)}-transcribed"

        output_path = self.writer.write_latex(base=base, name=name, preamble=preamble, body=text)

        artifact_fn = getattr(self.normalizer, "artifact_paths", None)
        artifact_paths = []
        if callable(artifact_fn):
            artifact_paths = [Path(p) for p in artifact_fn() if p]

        files = [output_path, *artifact_paths]

        limits = {
            "rpm": RATE_LIMITS.get(job.model),
            "tpm": TOKEN_LIMITS_PER_MINUTE.get(job.model),
        }

        events_path = base / "run-events.ndjson"
        self.monitor.flush_summary(
            to=base / "run-summary.json",
            cost=self.cost,
            job=job,
            files=files,
            limits=limits,
            ndjson=events_path,
        )
        return output_path

    def _infer_kind(self, assets: list[Asset]) -> Kind:
        if assets and assets[0].media == "video":
            return Kind.LECTURE
        if assets and assets[0].media == "image":
            return Kind.SLIDES
        return Kind.DOCUMENT

    def _modality_for(self, assets: list[Asset], pdf_mode: PdfMode) -> str:
        if assets and assets[0].media in {"video", "audio"}:
            return "video"
        return "pdf" if pdf_mode == PdfMode.PDF else "image"

    @staticmethod
    def _slug(value: str) -> str:
        return "".join(
            char if char.isalnum() or char in "-_." else "-" for char in value
        ).strip("-")
