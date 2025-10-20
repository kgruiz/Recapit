# Release Notes

## Unreleased

### Added

- New `plan`, `summarize`, and `init` CLI commands with preset profiles, subtitle exports (SRT/VTT), and configuration scaffolding.
- Composite ingestion covering local files, HTTP URLs, YouTube passthrough, and Google Drive downloads.
- Video normalization pipeline producing chunk manifests, per-chunk telemetry, and resumable artifacts.
- Gemini provider adapter with pricing-aware telemetry, run summaries, and NDJSON event logs.
- YAML-based configuration (`lecture-summarizer.yaml`) with preset overrides, video chunk settings, and CLI precedence.
- Prompt strategy package (`lecture_summarizer/prompts/`) with enriched video instructions and template fallback support.

### Changed

- CLI documentation updated to highlight presets, exports, and run summary artifacts.
- Cost estimation now loads pricing data from `pricing.yaml`, with per-model overrides.
- Default CLI command now routes through the new engine; `transcribe` remains as a friendly alias.

### Testing

- Expanded test suite to cover CLI `summarize`/`init`, ingestion routing (local/URL/YouTube/Drive), video chunk manifests, and Gemini provider telemetry stubs.
