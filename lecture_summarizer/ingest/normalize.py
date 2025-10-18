from __future__ import annotations

import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from ..core.types import Asset, PdfMode


class CompositeNormalizer:
    """Normalize assets for downstream processing."""

    def __init__(self, *, image_root: Path | None = None) -> None:
        self._image_root = Path(image_root or Path(tempfile.gettempdir()) / "lecture-pdf-pages")
        self._image_root.mkdir(parents=True, exist_ok=True)

    def normalize(self, assets: list[Asset], pdf_mode: PdfMode) -> list[Asset]:
        normalized: list[Asset] = []
        for asset in assets:
            if asset.media == "pdf":
                normalized.extend(self._normalize_pdf(asset, pdf_mode))
            else:
                normalized.append(asset)
        return normalized

    def _normalize_pdf(self, asset: Asset, pdf_mode: PdfMode) -> list[Asset]:
        if pdf_mode == PdfMode.PDF or pdf_mode == PdfMode.AUTO:
            return [asset]
        if pdf_mode != PdfMode.IMAGES:
            return [asset]

        page_count = self._count_pages(asset)
        if page_count == 0:
            return []

        output_dir = self._folder_for(asset)
        output_dir.mkdir(parents=True, exist_ok=True)
        generated: list[Asset] = []
        for idx in range(page_count):
            image_path = output_dir / f"{asset.path.stem}-p{idx:03d}.png"
            self._render_placeholder(image_path, page_number=idx + 1)
            generated.append(
                Asset(
                    path=image_path,
                    media="image",
                    page_index=idx,
                    source_kind=asset.source_kind,
                    mime="image/png",
                    meta={"source_pdf": str(asset.path)},
                )
            )
        return generated

    def _folder_for(self, asset: Asset) -> Path:
        safe = asset.path.stem or "document"
        return self._image_root / safe

    @staticmethod
    def _render_placeholder(path: Path, *, page_number: int) -> None:
        image = Image.new("RGB", (1024, 1325), color="white")
        draw = ImageDraw.Draw(image)
        text = f"Page {page_number}"
        draw.text((40, 40), text, fill="black")
        image.save(path, format="PNG")

    @staticmethod
    def _count_pages(asset: Asset) -> int:
        try:
            from PyPDF2 import PdfReader
        except ImportError:  # pragma: no cover - dependency should exist but keep fallback
            return 1

        try:
            reader = PdfReader(str(asset.path))
        except Exception:  # noqa: BLE001
            return 1

        pages = getattr(reader, "pages", [])
        try:
            return len(pages)
        except TypeError:  # pragma: no cover - defensive
            return 0
