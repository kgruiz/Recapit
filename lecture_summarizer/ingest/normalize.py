from __future__ import annotations

from ..core.types import Asset, PdfMode


class PassthroughNormalizer:
    """Return assets unchanged. Acts as a placeholder until full normalization lands."""

    def normalize(self, assets: list[Asset], pdf_mode: PdfMode) -> list[Asset]:  # pragma: no cover - trivial
        return list(assets)
