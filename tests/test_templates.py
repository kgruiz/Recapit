from lecture_summarizer.templates import DEFAULT_PROMPTS


def test_default_prompts_have_no_quotes_or_trailing_whitespace() -> None:
    for name, prompt in DEFAULT_PROMPTS.items():
        assert "\"" not in prompt, f"Prompt for {name} contains a double quote"
        assert prompt == prompt.strip(), f"Prompt for {name} has leading or trailing whitespace"
