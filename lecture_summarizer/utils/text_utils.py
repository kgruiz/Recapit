import re


def remove_code_block_syntax(text: str | list[str]) -> str:
    if not isinstance(text, (str, list)):
        raise TypeError(
            f'Parameter "text" must be a str or list[str]. Given type: "{type(text).__name__}"'
        )

    if isinstance(text, list):
        text = "\n".join(text) + "\n"
        text = text.strip()
    else:
        text = text.strip()

    if isinstance(text, str):
        text = text.splitlines()

    if text[0].strip().startswith("```") and text[-1].strip() == "```":
        text = text[1:-1]

    return "\n".join(text) + "\n"


def clean_response(
    combined_response: str,
    preamble: str,
    title: str | None = "",
    author: str | None = "",
    date: str | None = "",
) -> str:
    preamble_lines = preamble.splitlines()

    for line in preamble_lines:
        combined_response = re.sub(
            rf"^{re.escape(line)}$", "", combined_response, flags=re.MULTILINE
        )

    end_document_line = r"\end{document}"

    combined_response = re.sub(
        rf"^{re.escape(end_document_line)}$",
        r"\\newpage",
        combined_response,
        flags=re.MULTILINE,
    )

    title_line = r"\\title\{.*\}"
    author_line = r"\\author\{.*\}"
    date_line = r"\\date\{.*\}"

    other_lines = [title_line, author_line, date_line]

    for line in other_lines:
        combined_response = re.sub(
            rf"^{line}$", "", combined_response, flags=re.MULTILINE
        )

    combined_response = combined_response.strip()
    combined_response = re.sub(r"\n{2,}", "\n\n", combined_response)

    remaining_packages = re.findall(
        r"^\\usepackage\{([^\}]*)\}", combined_response, flags=re.MULTILINE
    )
    for package in remaining_packages:
        preamble_lines.insert(1, f"\\usepackage{{{package}}}")

    preamble = "\n".join(preamble_lines)

    if re.search(r"^\\title\{.*\}", preamble, flags=re.MULTILINE) is None:
        if title is not None:
            preamble_lines = preamble.splitlines()
            preamble_lines.insert(1, f"\\title{{{title}}}")
            preamble = "\n".join(preamble_lines)
    elif title is not None:
        preamble = re.sub(
            r"^\\title\{(.*)\}", rf"\\title{{{title}}}", preamble, flags=re.MULTILINE
        )
    else:
        preamble = re.sub(r"^\\title\{.*\}", "", preamble, flags=re.MULTILINE)

    if re.search(r"^\\author\{.*\}", preamble, flags=re.MULTILINE) is None:
        if author is not None:
            preamble_lines = preamble.splitlines()
            preamble_lines.insert(2, f"\\author{{{author}}}")
            preamble = "\n".join(preamble_lines)
    elif author is not None:
        preamble = re.sub(
            r"^\\author\{(.*)\}", rf"\\author{{{author}}}", preamble, flags=re.MULTILINE
        )
    else:
        preamble = re.sub(r"^\\author\{.*\}", "", preamble, flags=re.MULTILINE)

    if re.search(r"^\\date\{.*\}", preamble, flags=re.MULTILINE) is None:
        if date is not None:
            preamble_lines = preamble.splitlines()
            preamble_lines.insert(3, f"\\date{{{date}}}")
            preamble = "\n".join(preamble_lines)
    elif date is not None:
        preamble = re.sub(
            r"^\\date\{(.*)\}", rf"\\date{{{date}}}", preamble, flags=re.MULTILINE
        )
    else:
        preamble = re.sub(r"^\\date\{.*\}", "", preamble, flags=re.MULTILINE)

    cleaned_response = f"{preamble}\n{combined_response}\n{end_document_line}"
    return cleaned_response
