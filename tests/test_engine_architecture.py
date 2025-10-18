from __future__ import annotations

import json
from pathlib import Path
from lecture_summarizer.core.types import Asset, Job, Kind, PdfMode
from lecture_summarizer.engine.engine import Engine
from lecture_summarizer.engine.planner import Planner
from lecture_summarizer.ingest import LocalIngestor, CompositeNormalizer
from lecture_summarizer.output.cost import CostEstimator
from lecture_summarizer.render.writer import LatexWriter
from lecture_summarizer.telemetry import RunMonitor


class _FakeIngestor:
    def __init__(self, asset: Asset) -> None:
        self._asset = asset

    def discover(self, job: Job) -> list[Asset]:
        return [self._asset]


class _FakeNormalizer:
    def normalize(self, assets: list[Asset], pdf_mode: PdfMode) -> list[Asset]:
        return assets


class _FakePrompt:
    kind = Kind.DOCUMENT

    def preamble(self) -> str:
        return "Preamble"

    def instruction(self, preamble: str) -> str:
        return f"Instruction with {preamble}"


class _FakeProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def supports(self, capability: str) -> bool:  # pragma: no cover - unused but part of protocol
        return True

    def transcribe(self, *, instruction: str, assets: list[Asset], modality: str, meta: dict) -> str:
        self.calls.append({
            "instruction": instruction,
            "assets": assets,
            "modality": modality,
            "meta": meta,
        })
        return "Body content"


class _RecordingWriter(LatexWriter):
    def __init__(self) -> None:
        self.paths: list[Path] = []

    def write_latex(self, *, base: Path, name: str, preamble: str, body: str) -> Path:
        path = super().write_latex(base=base, name=name, preamble=preamble, body=body)
        self.paths.append(path)
        return path


def test_engine_runs_full_cycle(tmp_path: Path) -> None:
    asset_path = tmp_path / "test.pdf"
    asset_path.write_text("stub")
    asset = Asset(path=asset_path, media="pdf")

    provider = _FakeProvider()
    writer = _RecordingWriter()

    engine = Engine(
        ingestor=_FakeIngestor(asset),
        normalizer=_FakeNormalizer(),
        prompts={Kind.DOCUMENT: _FakePrompt()},
        provider=provider,
        writer=writer,
        monitor=RunMonitor(),
        cost=CostEstimator(),
    )

    job = Job(
        source=str(asset_path),
        recursive=False,
        kind=Kind.DOCUMENT,
        pdf_mode=PdfMode.PDF,
        output_dir=tmp_path,
        model="gemini-test",
    )

    output = engine.run(job)

    assert output is not None
    assert output.exists()
    run_summary = output.parent / "run-summary.json"
    assert run_summary.exists()
    payload = json.loads(run_summary.read_text())
    assert payload["summary"]["total_requests"] == 0
    assert provider.calls and provider.calls[0]["modality"] == "pdf"


def test_planner_reports_basic_plan(tmp_path: Path) -> None:
    sample = tmp_path / "demo.pdf"
    sample.write_text("stub")

    job = Job(
        source=str(sample),
        recursive=False,
        kind=None,
        pdf_mode=PdfMode.AUTO,
        output_dir=None,
        model="gemini-test",
    )

    planner = Planner(ingestor=LocalIngestor(), normalizer=CompositeNormalizer())
    report = planner.plan(job)

    assert report.assets, "Planner should discover local file assets"
    assert report.kind == Kind.DOCUMENT
    assert report.modality in {"pdf", "image"}
