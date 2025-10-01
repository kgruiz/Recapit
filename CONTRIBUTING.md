# Contributing to Lecture Summarizer

Thanks for your interest in improving Lecture Summarizer! This document outlines how to set up a development environment, coding standards, and expectations for pull requests.

## Quick Start

1. Fork the repository and clone your fork.
2. Create a virtual environment (recommended: `uv venv` or `python -m venv .venv`).
3. Activate the environment and install the project in editable mode:
   ```shell
   uv pip install -e .
   # or
   python -m pip install -e .
   ```
4. Set `GEMINI_API_KEY` in your shell before exercising the CLI or API.

### Dependencies

- Python 3.10+
- Poppler (required by `pdf2image` for rasterization)
- Google Gemini access with permissions for the models listed in `lecture_summarizer/constants.py`

## Development Workflow

1. Create a feature branch for your work.
2. Make focused changes with clear commits (see "Commit Messages").
3. Run the project checks before each commit/push:
   ```shell
   python -m compileall lecture_summarizer run.py
   # add project-specific tests/linters here as they become available
   ```
4. Verify that the CLI still works for the scenario you are touching (e.g., run `lecture-summarizer --help` or a sample command against fixture data).
5. Open a pull request describing the motivation, approach, and testing performed.

## Coding Standards

- Prefer dependency management with `uv` (Python). If other ecosystems are introduced, follow the repository defaults (`pnpm` for JS, `cargo`/`just` for Rust, etc.).
- Keep code ASCII unless existing files require otherwise.
- Add concise comments only when logic is non-obvious.
- Avoid global state; leverage the modular pipeline and configuration helpers.
- Follow existing directory structure (`lecture_summarizer/`, `templates/`, etc.).

### Templates & Prompts

Prompt templates in `templates/` are part of the API surface. When modifying them:
- Explain rationale in the PR description.
- Keep formatting consistent; avoid trailing spaces.
- Ensure the loader caches remain valid (file names must match the functions in `TemplateLoader`).

## Testing & Validation

- Do not skip or stub tests. If a failure surfaces, fix the root cause.
- When adding new functionality, include automated coverage where feasible (unit tests or integration scripts).
- For long-running features, consider providing sample input/output in the PR for manual validation.

## Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) to keep history readable. Common prefixes:

| Prefix | When to use |
| --- | --- |
| `feat:` | New user-facing functionality |
| `fix:` | Bug fixes |
| `docs:` | Documentation-only changes |
| `refactor:` | Internal code changes that don't alter behavior |
| `test:` | Adding or adjusting tests |
| `chore:` | Tooling, dependencies, or housekeeping |

Example:
```
feat: add bulk PDF transcription CLI flag
```

## Pull Request Checklist

- [ ] Tests and linting commands pass locally.
- [ ] Documentation updated (README, docstrings, or templates) if behavior changed.
- [ ] Commits are logically grouped and follow the Conventional Commits spec.
- [ ] Screenshots or logs attached when they help reviewers.

## Questions?

Open an issue or discussion thread describing your question, feature idea, or bug report. We appreciate clear reproduction steps and context (operating system, command executed, logs, etc.).
