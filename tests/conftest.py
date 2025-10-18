import sys
from pathlib import Path
import types

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.modules.setdefault("pillow_avif", types.ModuleType("pillow_avif"))

if "yaml" not in sys.modules:
    def _simple_safe_load(stream):  # pragma: no cover - trivial parser
        if hasattr(stream, "read"):
            content = stream.read()
        else:
            content = str(stream)
        root: dict[str, object] = {}
        stack: list[tuple[int, dict[str, object]]] = [(0, root)]
        for raw_line in str(content).splitlines():
            if not raw_line.strip() or raw_line.lstrip().startswith("#"):
                continue
            indent = len(raw_line) - len(raw_line.lstrip(" "))
            line = raw_line.strip()
            key, _, rest = line.partition(":")
            key = key.strip().strip('"')
            rest = rest.strip()
            while len(stack) > 1 and indent < stack[-1][0]:
                stack.pop()
            current = stack[-1][1]
            if not rest:
                value: dict[str, object] = {}
                current[key] = value
                stack.append((indent + 2, value))
            else:
                try:
                    numeric = float(rest)
                    value = numeric
                except ValueError:
                    value = rest.strip('"')
                current[key] = value
        return root

    yaml_mod = types.SimpleNamespace(safe_load=_simple_safe_load)
    sys.modules["yaml"] = yaml_mod

if "typer" not in sys.modules:
    typer_mod = types.ModuleType("typer")

    class _StubContext:  # pragma: no cover - trivial container
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _StubTyper:  # pragma: no cover - simplifying CLI usage in tests
        def __init__(self, *args, **kwargs):
            self.commands = {}

        def command(self, *args, **kwargs):
            def decorator(func):
                self.commands[func.__name__] = func
                return func

            return decorator

        def callback(self, *args, **kwargs):
            def decorator(func):
                self.commands["__callback__"] = func
                return func

            return decorator

        def add_typer(self, *args, **kwargs):
            return None

    def _option(default=None, *args, **kwargs):  # pragma: no cover - simple passthrough
        return default

    def _argument(default=None, *args, **kwargs):  # pragma: no cover - simple passthrough
        return default

    def _echo(message: str) -> None:  # pragma: no cover - print helper
        print(message)

    class _BadParameter(ValueError):
        pass

    class _Exit(SystemExit):
        pass

    typer_mod.Typer = _StubTyper
    typer_mod.Option = _option
    typer_mod.Argument = _argument
    typer_mod.echo = _echo
    typer_mod.BadParameter = _BadParameter
    typer_mod.Exit = _Exit
    typer_mod.Context = _StubContext
    sys.modules["typer"] = typer_mod

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
