from datetime import datetime, timezone

from lecture_summarizer.constants import GEMINI_2_5_FLASH
from lecture_summarizer.telemetry import RequestEvent, RunMonitor


def make_event(**kwargs) -> RequestEvent:
    defaults = {
        "model": GEMINI_2_5_FLASH,
        "modality": "pdf",
        "started_at": datetime.now(timezone.utc),
        "finished_at": datetime.now(timezone.utc),
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "metadata": {},
    }
    defaults.update(kwargs)
    return RequestEvent(**defaults)


def test_run_monitor_summary_and_costs_with_estimates():
    monitor = RunMonitor()

    text_event = make_event(
        input_tokens=1_000,
        output_tokens=2_000,
        total_tokens=3_000,
        metadata={"source_path": "doc.pdf"},
    )
    monitor.record(text_event)

    video_event = make_event(
        modality="video",
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
        metadata={
            "chunk_start_seconds": 0.0,
            "chunk_end_seconds": 2.0,
            "source_path": "clip.mp4",
        },
    )
    monitor.record(video_event)

    summary = monitor.summarize()
    assert summary.total_requests == 2
    assert summary.total_input_tokens == 1_000
    assert summary.total_output_tokens == 2_000

    costs = monitor.costs()
    assert costs.total_cost > 0
    assert costs.estimated is True
    assert GEMINI_2_5_FLASH in costs.per_model
    model_cost = costs.per_model[GEMINI_2_5_FLASH]
    assert model_cost["total_cost"] >= costs.total_cost
