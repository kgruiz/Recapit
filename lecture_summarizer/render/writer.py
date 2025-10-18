from __future__ import annotations

from pathlib import Path


class LatexWriter:
    """Persist LaTeX documents for engine results."""

    def write_latex(self, *, base: Path, name: str, preamble: str, body: str) -> Path:
        base.mkdir(parents=True, exist_ok=True)
        target = base / f"{name}.tex"
        with target.open("w", encoding="utf-8") as handle:
            handle.write("\\documentclass{article}\n")
            handle.write("\\usepackage{hyperref,amsmath}\n")
            handle.write("\\begin{document}\n")
            handle.write(preamble.rstrip() + "\n\n")
            handle.write(body.rstrip() + "\n")
            handle.write("\\end{document}\n")
        return target
