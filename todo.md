# TODO: Video Input Support & Project Enhancements (updated 2025-10-15)

## Video Ingestion Pipeline
- [x] Audit `lecture_summarizer.cli`, `api`, and `pipeline` to catalogue the current PDF/image flow and pinpoint where video sources and a new `Kind.VIDEO` need to plug in (CLI options, auto-kind inference, pipeline wiring).
- [x] Update file discovery to recognize common video extensions (`*.mp4`, `*.mov`, `*.webm`, etc.) and optional YouTube URLs; expose CLI/API switches like `--include-video`, `--video-pattern`, and `--video-model` so users opt in explicitly.
- [x] Design the chunk-processing contract (structures for chunk metadata, offsets, fps) and decide how manifests live under `pickles/` for resumable runs before writing any code.
- [x] Prototype a dedicated `video.py` utility module that wraps ffmpeg (or `ffmpeg-python`) to normalize codecs, extract duration, and split videos exceeding Gemini limits (≤20 MB inline uploads or ≈2 h per Files API call) while producing ISO-8601 `start_offset`/`end_offset` pairs.
- [x] Spike the Files API workflow: staged upload, polling `files.get` until `state == "ACTIVE"`, then `models.generate_content` requests that place the video part before instructions and thread through `videoMetadata`, `mediaResolution`, `thinking_budget`, and `include_thoughts` flags.

## Chunk Assembly & Outputs
- [ ] Specify how chunk-level responses (transcripts, visual summaries, Q&A) are stitched—define merge order, timestamp normalization to `MM:SS`, and LaTeX cleanup rules for multimodal cues like “[Slide]”.
- [ ] Plan storage layout for per-chunk artifacts: raw JSON/text, optional SRT/VTT, combined LaTeX/Markdown/JSON, and a machine-readable manifest capturing model, fps, duration, and file URIs.
- [ ] Map how existing `pipeline.Pipeline._combine_and_write` should branch for video to merge multiple chunk payloads without losing per-segment context.
- [ ] Decide how cached chunk manifests enable resume-on-interrupt, including checksum or mtime tracking to detect source changes before reusing uploads.

## Configuration & Templates
- [ ] Extend `AppConfig` and environment variables (`LECTURE_SUMMARIZER_VIDEO_MAX_DURATION`, `..._DEFAULT_FPS`, `..._USE_YOUTUBE`) so video behavior is user-tunable and surfaced via CLI help text.
- [ ] Create new prompt templates under `templates/video-*` for transcript-only, timeline summary, and slide+visual descriptions; document how users override these just like existing PDF prompts.
- [ ] Ensure system instructions incorporate Gemini best practices (media-first ordering, explicit requests for transcripts + visual cues, optional `response_schema` for structured JSON timelines).

## Rate Limiting & Robustness
- [ ] Revisit `TokenBucket` settings using Gemini’s video token guidance (≈300 tokens/sec default, ≈100 tokens/sec low resolution) and evaluate preflight `models.count_tokens` calls to throttle before hitting API limits.
- [ ] Draft retry/backoff logic for Files API uploads, plus clear error surfaces when uploads fail or models reject oversized chunks (include actionable remediation hints).
- [ ] Plan verbose/debug logging that captures chunk boundaries, file URIs, token estimates, and any `thought_signature` data when `include_thoughts` is enabled.

## Validation Strategy
- [ ] Identify unit tests required for the chunker (duration math, offset accuracy, manifest serialization) and `LLMClient` video upload mock flows.
- [ ] Outline integration tests covering CLI runs with `--include-video`, ensuring chunk splitting, resumability, and combined outputs behave as expected.
- [ ] Select or generate fixture media (≤30 s sample video) for smoke tests, and document how to execute longer acceptance tests locally without bloating CI artifacts.

## Documentation & Release Prep
- [ ] Update README, CLI `--help`, and docstrings with video prerequisites (ffmpeg, Gemini quotas/pricing), new options, and examples demonstrating auto-splitting behavior.
- [ ] Prepare release notes/CHANGELOG describing the new modality, configuration knobs, and any dependency additions.
- [ ] Plan a version bump and final verification checklist (lint, format, targeted tests) before merging, ensuring no residual TODOs remain.
