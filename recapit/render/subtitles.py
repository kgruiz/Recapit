from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List


def _format_timestamp(seconds: float, *, kind: str) -> str:
    total_ms = max(int(round(seconds * 1000)), 0)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    sec, ms = divmod(remainder, 1000)
    if kind == "srt":
        return f"{hours:02d}:{minutes:02d}:{sec:02d},{ms:03d}"
    return f"{hours:02d}:{minutes:02d}:{sec:02d}.{ms:03d}"


def _split_text(text: str, parts: int) -> List[str]:
    cleaned = text.strip()
    if not cleaned:
        return [""] * max(parts, 1)
    paragraphs = [p.strip() for p in cleaned.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [cleaned]
    if parts <= 1:
        return ["\n".join(paragraphs)]
    segments: List[str] = [""] * parts
    for idx, paragraph in enumerate(paragraphs):
        target = idx % parts
        if segments[target]:
            segments[target] += "\n\n"
        segments[target] += paragraph
    return segments


@dataclass
class SubtitleExporter:
    """Generate simple SRT and VTT exports from aggregated transcript text."""

    def write(
        self,
        fmt: str,
        *,
        base: Path,
        name: str,
        text: str,
        chunks: Iterable[dict] | None,
    ) -> Path | None:
        fmt_lower = fmt.lower()
        if fmt_lower not in {"srt", "vtt"}:
            return None
        base.mkdir(parents=True, exist_ok=True)
        target = base / f"{name}.{fmt_lower}"
        chunk_list = list(chunks or [])
        if not chunk_list:
            chunk_list = [{"start_seconds": 0.0, "end_seconds": 0.0}]
        segments = _split_text(text, len(chunk_list))
        lines: List[str] = []
        if fmt_lower == "vtt":
            lines.append("WEBVTT\n")
        for idx, chunk in enumerate(chunk_list):
            start = float(chunk.get("start_seconds", idx * 5.0))
            end = float(chunk.get("end_seconds", start + 5.0))
            content = segments[idx] if idx < len(segments) else ""
            if fmt_lower == "srt":
                lines.append(str(idx + 1))
                lines.append(f"{_format_timestamp(start, kind='srt')} --> {_format_timestamp(end, kind='srt')}")
                lines.append(content or "[No content]")
                lines.append("")
            else:
                lines.append(f"{_format_timestamp(start, kind='vtt')} --> {_format_timestamp(end, kind='vtt')}")
                lines.append(content or "[No content]")
                lines.append("")
        target.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
        return target
