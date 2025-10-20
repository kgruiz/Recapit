from pathlib import Path


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def slugify(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in name).strip("-")
