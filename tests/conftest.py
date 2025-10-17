import sys
from pathlib import Path
import types

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.modules.setdefault("pillow_avif", types.ModuleType("pillow_avif"))

if "httpx" not in sys.modules:
    dummy_httpx = types.ModuleType("httpx")

    class _DummyTimeout:  # pragma: no cover - simple stub
        def __init__(self, *args, **kwargs):
            pass

    dummy_httpx.Timeout = _DummyTimeout
    sys.modules["httpx"] = dummy_httpx

if "google" not in sys.modules:
    google_mod = types.ModuleType("google")
    genai_mod = types.ModuleType("google.genai")

    class _DummyFiles:  # pragma: no cover - simple stub
        def upload(self, **kwargs):
            return types.SimpleNamespace(uri="stub:file", name="stub:file", mime_type="application/octet-stream")

        def get(self, **kwargs):
            return types.SimpleNamespace(state=types.SimpleNamespace(name="ACTIVE"))

    class _DummyModels:  # pragma: no cover - simple stub
        def generate_content(self, **kwargs):
            return types.SimpleNamespace(text="", usage_metadata=None)

        def count_tokens(self, **kwargs):
            return types.SimpleNamespace(total_tokens=0)

    class _DummyClient:  # pragma: no cover - simple stub
        def __init__(self, *args, **kwargs):
            httpx_client = types.SimpleNamespace(timeout=None)
            self._api_client = types.SimpleNamespace(_httpx_client=httpx_client)
            self.files = _DummyFiles()
            self.models = _DummyModels()

    genai_mod.Client = _DummyClient
    genai_mod.types = types.SimpleNamespace(
        HttpOptions=lambda **kwargs: kwargs,
        UploadFileConfig=lambda **kwargs: kwargs,
        GenerateContentConfig=lambda **kwargs: kwargs,
        ThinkingConfig=lambda **kwargs: kwargs,
        CountTokensConfig=lambda **kwargs: kwargs,
        FileData=lambda **kwargs: kwargs,
        Part=lambda **kwargs: kwargs,
        Content=lambda **kwargs: kwargs,
    )

    google_mod.genai = genai_mod
    sys.modules["google"] = google_mod
    sys.modules["google.genai"] = genai_mod

sys.modules.setdefault("pdf2image", types.SimpleNamespace(convert_from_path=lambda *args, **kwargs: []))
sys.modules.setdefault(
    "PyPDF2",
    types.SimpleNamespace(PdfReader=lambda *args, **kwargs: types.SimpleNamespace(pages=[])),
)
