# LectureSummarizer (modular)

## CLI
```bash
export GEMINI_API_KEY=...
lecture-summarizer slides /path/to/slides
lecture-summarizer lectures /path/to/lectures
lecture-summarizer documents /path/to/pdfs --recursive
lecture-summarizer images /path/to/imgs --pattern "*.png"
lecture-summarizer latex-md /path/to/texdir
lecture-summarizer latex-json /path/to/texdir
```

## Library

```python
from lecture_summarizer import TranscribeDocuments
TranscribeDocuments("/path/to/dir")
```
