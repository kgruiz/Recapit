from pathlib import Path

import subprocess

import pytest

from lecture_summarizer.video import (
    VideoEncoderPreference,
    normalize_video,
    select_encoder_chain,
)


def test_select_encoder_chain_auto_prefers_available_hardware(monkeypatch):
    monkeypatch.setattr(
        "lecture_summarizer.video._ffmpeg_encoder_names",
        lambda: {"h264_nvenc"},
    )
    chain, diagnostics = select_encoder_chain(VideoEncoderPreference.AUTO)
    assert chain[0].codec == "h264_nvenc"
    assert chain[-1].codec == "libx264"
    assert any("detected FFmpeg encoder" in msg for msg in diagnostics)


def test_select_encoder_chain_auto_falls_back_when_missing(monkeypatch):
    monkeypatch.setattr(
        "lecture_summarizer.video._ffmpeg_encoder_names",
        lambda: set(),
    )
    chain, diagnostics = select_encoder_chain(VideoEncoderPreference.AUTO)
    assert chain[0].codec == "libx264"
    assert any("no hardware encoder detected" in msg for msg in diagnostics)


def test_select_encoder_chain_specific_preference_handles_missing(monkeypatch):
    monkeypatch.setattr(
        "lecture_summarizer.video._ffmpeg_encoder_names",
        lambda: set(),
    )
    chain, diagnostics = select_encoder_chain(VideoEncoderPreference.VIDEOTOOLBOX)
    assert chain[0].codec == "libx264"
    assert any("not available" in msg for msg in diagnostics)


def test_normalize_video_falls_back_to_cpu_on_failure(monkeypatch, tmp_path):
    # Pretend both NVENC and libx264 are available so the chain tries NVENC first.
    monkeypatch.setattr(
        "lecture_summarizer.video._ffmpeg_encoder_names",
        lambda: {"h264_nvenc", "libx264"},
    )
    chain, _ = select_encoder_chain(VideoEncoderPreference.NVENC)

    input_path = tmp_path / "sample.mp4"
    input_path.write_bytes(b"\x00" * 8)

    output_dir = tmp_path / "normalized"

    def fake_run(cmd, check, capture_output):
        if "h264_nvenc" in cmd:
            raise subprocess.CalledProcessError(returncode=1, cmd=cmd, stderr="nvenc failure")
        if "libx264" in cmd:
            output_path = Path(cmd[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"normalized")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("lecture_summarizer.video.subprocess.run", fake_run)

    result = normalize_video(input_path, output_dir=output_dir, encoder_chain=chain)
    assert result.encoder.codec == "libx264"
    assert not result.reused_existing
    assert any("nvenc" in msg for msg in result.diagnostics)
    assert (output_dir / "sample-normalized.mp4").exists()
