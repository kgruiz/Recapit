import re
from typing import Iterable


def strip_code_fences(text: str | Iterable[str]) -> str:
    if isinstance(text, str):
        lines = text.strip().splitlines()
    else:
        lines = [*text]
    if not lines:
        return ""
    if lines[0].strip().startswith("```") and lines[-1].strip() == "```":
        lines = lines[1:-1]
    return ("\n".join(lines)).strip() + "\n"


def clean_latex(
    combined: str,
    preamble: str,
    *,
    title: str | None = "",
    author: str | None = "",
    date: str | None = "",
) -> str:
    # Remove duplicated preamble lines from combined
    for line in preamble.splitlines():
        combined = re.sub(rf"^{re.escape(line)}$", "", combined, flags=re.MULTILINE)

    # Replace stray \end{document} with newpage
    combined = re.sub(r"^\\end{document}$", r"\\newpage", combined, flags=re.MULTILINE)
    combined = combined.strip()
    combined = re.sub(r"\n{2,}", "\n\n", combined)

    # Ensure title/author/date present in preamble
    def upsert(field: str, value: str | None, text: str) -> str:
        pat = rf"^\\{field}\{{.*\}}"
        if value is None:
            return re.sub(pat, "", text, flags=re.MULTILINE)
        if re.search(pat, text, flags=re.MULTILINE):
            return re.sub(pat, rf"\\{field}{{{value}}}", text, flags=re.MULTILINE)
        lines = text.splitlines()
        insert_idx = 1 if field == "title" else 2 if field == "author" else 3
        lines.insert(insert_idx, rf"\\{field}{{{value}}}")
        return "\n".join(lines)

    preamble = upsert("title", title, preamble)
    preamble = upsert("author", author, preamble)
    preamble = upsert("date", date, preamble)

    return f"{preamble}\n{combined}\n\\end{{document}}"
