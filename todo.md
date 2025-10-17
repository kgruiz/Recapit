# TODO: Video Input Support & Project Enhancements (updated 2025-10-15)

## Video Ingestion Pipeline
- [x] Audit `lecture_summarizer.cli`, `api`, and `pipeline` to catalogue the current PDF/image flow and pinpoint where video sources and a new `Kind.VIDEO` need to plug in (CLI options, auto-kind inference, pipeline wiring).
- [x] Update file discovery to recognize common video extensions (`*.mp4`, `*.mov`, `*.webm`, etc.) and optional YouTube URLs; expose CLI/API switches like `--include-video`, `--video-pattern`, and `--video-model` so users opt in explicitly.
- [x] Design the chunk-processing contract (structures for chunk metadata, offsets, fps) and decide how manifests live under `pickles/` for resumable runs before writing any code.
- [x] Prototype a dedicated `video.py` utility module that wraps ffmpeg (or `ffmpeg-python`) to normalize codecs, extract duration, and split videos exceeding Gemini limits (≤20 MB inline uploads or ≈2 h per Files API call) while producing ISO-8601 `start_offset`/`end_offset` pairs.
- [x] Spike the Files API workflow: staged upload, polling `files.get` until `state == "ACTIVE"`, then `models.generate_content` requests that place the video part before instructions and thread through `videoMetadata`, `mediaResolution`, `thinking_budget`, and `include_thoughts` flags.

## Concurrency, Quotas & Cost Tracking
- [x] Refactor `TokenBucket` with thread-safe primitives (lock + condition) and expose async-compatible wrappers so parallel workers share accurate quota accounting across PDF, image, and video flows.
- [x] Introduce a concurrency controller (thread pool / task group) in `pipeline` and API entrypoints to parallelize per-file work and per-chunk video transcription when chunking is already required, with configuration knobs for worker counts.
- [x] Instrument every Gemini call to capture request metadata (model, modality, start/end timestamps, tokens, chunk identifiers) and surface it via structured logging plus an aggregated run report.
- [x] Build a quota monitor that tracks per-model RPM/TPM and concurrent upload caps using the published limits (≤2 GB per file, ≤20 GB storage, ≤100 concurrent batch jobs); emit pre-emptive sleeps or warnings before hitting 80% utilization and handle `429`/quota errors with exponential backoff.
- [x] Add cost estimation by multiplying observed input/output tokens (or chunk durations) against the pricing table in `gemini-api-docs.md`, storing per-run totals and cumulative spend summaries.
- [x] Surface monitoring output to the CLI (`--show-quota`, `--cost-summary`) and persist to JSON in the run directory so downstream automation can react (alerts, budgeting dashboards).
- [x] Enrich command outputs with token usage, estimated spend, and related stats by default, with flags (`--show-summary/--hide-summary`, `--detailed-costs`, `--summary-path`) to adjust the level of detail or persistence.
- [x] Write unit/integration tests that simulate quota exhaustion, cost calculations, and threaded chunk execution to ensure monitoring remains accurate under parallel load.

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
