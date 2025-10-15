# Gemini API Reference Notes
Date: 2025-10-15
Scope: Consolidated highlights from current Gemini developer documentation covering capabilities, workflows, limits, and pricing across modalities.

---

## 1. Core APIs & Capabilities

### 1.1 Text & Multimodal Generation
- **Endpoints:** `models.generate_content`, `models.generate_content_stream`, `models.count_tokens`; chat helpers in SDKs wrap these endpoints.
- **Inputs:** Text, images, video, audio, inline blobs (≤20 MB), or file references uploaded via Files API. Multimodal prompts interleave parts; media-first ordering recommended when a single image/video is supplied.
- **Outputs:** Free-form text, Markdown, HTML, LaTeX, JSON (with or without schemas). Streaming (`generate_content_stream`) yields incremental parts; SSE endpoint (`streamGenerateContent`) available via REST.
- **Thinking budgets:** Gemini 2.5 models default to dynamic thinking; set `thinking_budget=0` to disable only on Flash/Flash-Lite (range 0–24 576), use positive integers to cap (Pro range 128–32 768, cannot be disabled), and `-1` to let the model decide dynamically. Enable `include_thoughts` for summaries; preserve `thought_signature` fields when manually editing history across turns.
- **Function calling:** Declare functions with `tools.functionDeclarations` (subset of OpenAPI schema). Modes: `AUTO` (default), `ANY` (force the model to respond with a function call), `NONE` (disable). Supports parallel function calls, compositional chains, MCP tool adapters, and integration with code execution/search tools. Python SDK can auto-run callables.
- **Structured output:** `response_schema`/`response_mime_type` enforce JSON structures; `response_json_schema` (preview) accepts broader JSON Schema constructs (limited recursion). Respect `propertyOrdering` to keep fields in deterministic sequence.
- **Sampling controls:** Configure `temperature`, `topP`, `topK`, and `stopSequences` via `generationConfig`. Gemini 2.5 Flash defaults to thinking-on; set budgets + temperature as needed.
- **System instructions:** Supply persistent behaviors (e.g., persona, formatting) via `GenerateContentConfig.system_instruction`; combine with conversation history for consistent tone.
- **Grounding & tools:** Add `{"google_search":{}}`, code execution, or custom MCP tools in the `tools` array to augment responses with external data or actions.

### 1.2 Video Understanding (Gemini 2.x & 2.5)
- **Input methods:**
  1. Files API upload (`files.upload`, `files.get`) → reference `file_uri`.
  2. Inline base64 (`inline_data`) when entire request ≤20 MB.
  3. YouTube URL ingestion (preview; public videos only, limited to ~8 h/day free).
- **Supported MIME types:** `video/mp4`, `video/mpeg`, `video/mov`, `video/avi`, `video/x-flv`, `video/mpg`, `video/webm`, `video/wmv`, `video/3gpp`. Normalizing to H.264/AAC MP4 improves consistency.
- **Metadata controls:**
  - `videoMetadata.start_offset` / `end_offset` (ISO 8601 duration strings, e.g., `"1250s"` or `"PT20S"`).
  - `videoMetadata.fps` to override default 1 fps sampling (raise for fast-changing visuals, lower for static lectures).
  - Optional `videoMetadata` via YouTube integration to clip segments before download.
- **Processing characteristics:** Files are sampled at 1 frame/second and audio at 1 kbps; expect roughly 300 tokens/sec at default media resolution or ~100 tokens/sec with `mediaResolution=LOW`.
- **Use cases:** Full lecture transcription, timestamped outlines, visual descriptions, question answering about specific times, summarization with key events. Include explicit prompts (e.g., “Provide [MM:SS] timestamps and describe slide content.”).
- **Token considerations:** Gemini 2.5 Pro/Flash (2 M context) handle ≈2 h at default resolution (≈6 h low); 1 M context models ≈1 h default (≈3 h low). Segment long videos or use `videoMetadata` clipping for efficiency.
- **Best practices:** Position video parts before text instructions, request both audio transcript and visual summary, increase FPS where necessary, and note that higher FPS may trigger different safety frames.

### 1.3 Audio Understanding & Generation
- **Audio ingestion:** Same workflows as video (Files API or inline base64 ≤20 MB). Supports URLs via Live API microphone streaming.
- **Supported MIME types:** `audio/wav`, `audio/mp3`, `audio/aiff`, `audio/aac`, `audio/ogg`, `audio/flac`. Ensure mono or stereo; Gemini downmixes to single-channel 16 kbps internally.
- **Audio-to-text tasks:** Full transcripts, timestamped transcripts (`MM:SS`), combined audio/visual description when paired with video frames, question answering (“What happens at 02:30?”), sentiment or speaker notes.
- **Native audio output:** Gemini 2.5 Flash Native Audio (preview) and the Flash/Pro TTS models produce speech with configurable voice, pace, emotion, and verbosity. Live API enables real-time, interactive voice conversations.
- **Tokenization & limits:** ≈32 tokens/sec of audio; max input ~9.5 hours across combined audio files. For multiple files, ensure total duration stays within limit.
- **Inline guidance:** If transcripts must include timestamps, specify format; for segmentation, pair prompts with `videoMetadata` or separate audio clips to control context windows.

### 1.4 Images & Video Generation
- **Image models:** Gemini 2.5 Flash Image (preview), Imagen 4 (fast/standard/ultra), Imagen 3. Accept text-only prompts or multimodal prompts with reference images (via Files API or inline).
- **Video models:** Veo 3.1 (preview), Veo 3, Veo 2 generate videos with or without audio; specify duration, camera directions, aspect ratio, and audio inclusion. Billing is per generated second.
- **Image understanding extras:** Gemini models support object detection (returns `box_2d` in [ymin, xmin, ymax, xmax] scaled 0–1000) and segmentation masks (base64 PNG probability maps). Setting `fps` in `videoMetadata` with inline video extracts frames at desired rate.
- **Prompt ordering:** For single-image prompts, place image part first; for multi-image prompts, interleave logically with text instructions.
- **Best practices:** Request structured outputs (use `[ "label": "...", "box_2d": [...] ]` etc.), mention desired frame rate or clipping for video tasks, and provide fallback instructions if detection fails.

### 1.5 Embeddings
- **Model:** `gemini-embedding-001` (successor to embedding gecko).
- **Features:** Matryoshka representation learning (MRL) allows output dimension control (`output_dimensionality` 128–3072; recommended 768/1536/3072).
- **Task types:** `SEMANTIC_SIMILARITY`, `CLASSIFICATION`, `CLUSTERING`, `RETRIEVAL_DOCUMENT`, `RETRIEVAL_QUERY`, `CODE_RETRIEVAL_QUERY`, `QUESTION_ANSWERING`, `FACT_VERIFICATION`. Set `task_type` to optimize embedding behavior.
- **Normalization:** 3072-dim outputs already normalized; smaller dimensions should be L2-normalized client-side for best similarity performance.
- **Performance notes:** Approximate MTEB scores—2048 dims ~68.16, 1536 dims ~68.17, 768 dims ~67.99, 512 dims ~67.55, 256 dims ~66.19, 128 dims ~63.31 (from docs).
- **Batch usage:** `batches.create_embeddings` with JSONL or inline lists reduces cost by half.
- **Storage:** Use vector databases or Cloud databases with vector support (BigQuery, AlloyDB, Cloud SQL) for retrieval pipelines.

### 1.6 Batch API
- **Purpose:** Asynchronous processing of large request sets at ~50% token cost.
- **Workflow:** Prepare JSONL with `GenerateContentRequest` objects → upload via Files API → `models:batchGenerateContent` → poll `batches/{id}` → download results JSONL.
- **Limits:** Input file ≤2 GB, up to 100 concurrent jobs, per-model batch token quotas (see §3).
- **Lifecycle:** Monitor `batch.state` transitions (`JOB_STATE_PENDING`, `JOB_STATE_RUNNING`, success/failure states). Completed jobs specify counters in `batchStats`.
- **Result parsing:** Each JSONL line includes original `key`, `response` or `error`. Unsuccessful entries should be retried separately.
- **Use cases:** Evaluations, large corpus transcription, large embedding ingestion.

### 1.7 Files API
- Upload media/documents (≤2 GB each, 20 GB total storage, retained 48 hours).
- Supports resumable uploads, retrieving metadata, deleting.
- Required for larger video/audio/document ingestion.
- Files cannot be downloaded back from API (secured); keep local copies if needed.
- **Headers & commands:** Start with `start`, then send bytes with `upload, finalize`. Include `X-Goog-Upload-Header-Content-Length` and `X-Goog-Upload-Header-Content-Type`.
- **States:** `PROCESSING`, `ACTIVE`, `FAILED`. Retry or re-upload on failure.
- **Metadata:** `files.get` returns URI (`file.uri`), MIME, display name, checksum, state.
- **Retention:** Files auto-expire after 48 hours; delete earlier to reclaim quota.

### 1.8 Context Caching (explicit caching)
- Cache reusable prompt content and media for cost savings.
- Set TTL (default 1 h) or explicit expiration.
- Pricing per cached token plus hourly storage (varies by model).
- Useful when generating multiple outputs from same large context.
- Updates limited to TTL/expiry changes; cached content itself immutable.
- Minimum cache size: 1 024 tokens for Gemini 2.5 Flash series, 2 048 tokens for Gemini 2.5 Pro; caches auto-expire when TTL reached.
- API workflow: `caches.create` → `caches.get`/`caches.list` to inspect metadata (`usage_metadata.cached_content_token_count`) → supply `GenerateContentConfig.cached_content` in subsequent calls → `caches.update` to adjust TTL/expiry → `caches.delete` to remove early.
- Implicit caching already enabled for Gemini 2.5 models—developers gain cost savings automatically without explicit caches.

### 1.9 Document Understanding (PDFs & long-form docs)
- **Inputs:** PDF files via Files API (recommended for >20 MB), inline base64 for smaller docs. Handles up to ~1 000 pages (≈1000 tokens/page).
- **Processing:** Gemini extracts text, images, diagrams, tables, and layout (beyond basic OCR). Supports direct PDF ingestion (`inline_data` or file uploads).
- **Large document flow:** Use Files API for remote URLs or local files, then `generate_content` with prompt describing tasks (summaries, Q&A, table extraction). For multiple PDFs, model accepts arrays of file parts (up to context limit).
- **Best practices:** Rotate pages correctly, avoid blurry scans, place text prompt after document part when using a single page.
- **Rate considerations:** Similar tokenization as images/frames (each page scaled ≤3072 px). Use batching and caching for repeated queries.
- **Use cases:** Summarization, table extraction to JSON, answering questions about content, conversion to Markdown/HTML.

---

## 2. Rate Limits (Free Tier vs Paid Tier 1)

| Model | Free RPM / TPM / RPD | Tier 1 RPM / TPM / RPD | Batch Enqueued Tokens (Tier 1) |
|-------|----------------------|------------------------|--------------------------------|
| Gemini 2.5 Pro | 5 · 125 k · 100 | 150 · 2 M · 10 k | 5 M |
| Gemini 2.5 Flash | 10 · 250 k · 250 | 1 000 · 1 M · 10 k | 3 M |
| Gemini 2.5 Flash-Lite | 15 · 250 k · 1 000 | 4 000 · 4 M · unlimited | 10 M |
| Gemini 2.0 Flash | 15 · 1 M · 200 | 2 000 · 4 M · unlimited | 10 M |
| Gemini 2.0 Flash-Lite | 30 · 1 M · 200 | 4 000 · 4 M · unlimited | 10 M |
| Gemini Flash Image (preview) | 500 · 500 k · 2 000 | 2 000 · 1.5 M · 50 k | — |
| Imagen 4 Standard | 10 · — · 70 | 15 · — · 1 000 | — |
| Imagen 4 Ultra | 5 · — · 30 | 10 · — · 400 | — |
| Veo 3 / 3.1 | 2 sessions | 4 sessions | — |
| Gemini Embedding | 100 · 30 k · 1 000 | 3 000 · 1 M · — | — |
| Gemini 2.5 Flash Native Audio | — (preview) | — | — |
| Gemini 2.5 Computer Use Preview | — | — | — |
| Gemma 3 / 3n | 30 · 15 k · 14 400 | same | — |

> **Notes:**
> - Higher tiers (Tier 2/3) further raise RPM/TPM (e.g., Flash up to 10 k RPM).
> - `sessions` entries refer to Live API concurrent sessions.
> - Grounding with Google Search has shared quota: free up to 500 RPD across Flash & Flash-Lite; 1 500 RPD free on paid tiers before per-1 000 charges.

---

## 3. Pricing (October 2025)

### 3.1 Text & Multimodal (per 1 M tokens unless noted)
> **Data usage:** Free-tier calls (and trial quotas) are used to improve Google products; paid-tier usage is not.
| Model | Input | Output (incl. thinking) | Batch Input | Batch Output | Context Caching | Notes |
|-------|-------|--------------------------|-------------|---------------|-----------------|-------|
| Gemini 2.5 Pro | $1.25 (≤200k) / $2.50 (>200k) | $10.00 / $15.00 | $0.625 / $1.25 | $5.00 / $7.5 | $0.125 / $0.25 + $4.50/hr | No free tier charges. |
| Gemini 2.5 Flash | $0.30 (text/img/video) · $1.00 (audio) | $2.50 | $0.15 · $0.50 (audio) | $1.25 | $0.03 · $0.10 + $1/hr | Live API: text $0.50 in/$2 out; audio/video $3/$12. |
| Gemini 2.5 Flash Preview | Same as Flash (context cache $0.0375 text) | Same | Same | Same | Slightly higher caching | Preview behavior. |
| Gemini 2.5 Flash-Lite | $0.10 · $0.30 (audio) | $0.40 | $0.05 · $0.15 | $0.20 | $0.025 · $0.125 + $1/hr | Most cost-efficient. |
| Gemini 2.5 Flash-Lite Preview | Same as Flash-Lite | Same | Same | Same | Same | Preview. |
| Gemini 2.0 Flash | $0.10 · $0.70 (audio) | $0.40 | $0.05 · $0.35 | $0.20 | $0.025 · $0.175 + $1/hr | Live API text $0.35/$1.50; audio/video $2.10/$8.50. |
| Gemini 2.0 Flash-Lite | $0.075 | $0.30 | $0.0375 | $0.15 | N/A | No caching/v2 features. |
| Gemini 2.5 Flash Native Audio (preview) | $0.50 (text) / $3.00 (audio/video) | $2.00 (text) / $12.00 (audio) | — | — | — | Real-time voice interactions. |
| Gemini 2.5 Flash Preview TTS | $0.50 (text) | $10.00 (audio) | $0.25 | $5.00 | — | Text-to-speech. |
| Gemini 2.5 Pro Preview TTS | $1.00 | $20.00 | $0.50 | $10.00 | — | High fidelity TTS. |
| Gemini 2.5 Computer Use Preview | $1.25 (≤200k) / $2.50 (>200k) | $10.00 / $15.00 | — | — | — | Specialized agent model. |

### 3.2 Image Generation (per image)
| Model | Price | Notes |
|-------|-------|-------|
| Gemini 2.5 Flash Image (preview) | $0.039 | Up to 1024×1024 (~1 290 tokens). Batch $0.0195. |
| Imagen 4 Fast / Standard / Ultra | $0.02 / $0.04 / $0.06 | Preview; high quality text rendering. |
| Imagen 3 | $0.03 | Stable; paid tier only. |

### 3.3 Video Generation (per second generated)
| Model | Price | Notes |
|-------|-------|-------|
| Veo 3.1 Standard / Fast | $0.40 / $0.15 | Preview; includes audio. |
| Veo 3 Standard / Fast | $0.40 / $0.15 | Stable release. |
| Veo 2 | $0.35 | Legacy option. |

### 3.4 Embeddings
| Model | Price | Batch Price | Notes |
|-------|-------|-------------|-------|
| `gemini-embedding-001` | $0.15 / 1 M tokens | $0.075 / 1 M tokens | Free for limited usage; Matryoshka dimensions. |

---

## 4. Tooling & SDK Highlights

### 4.1 Python (`google-genai`)
- Automatic function calling, files upload helpers, streaming.
- Converters for Pydantic schemas to `response_schema`.
- `TokenBucket` and config utilities manage rate limits.
- Async support: `client.aio` namespace mirrors sync API for concurrency (e.g., `await client.aio.models.generate_content(...)`).
- Live examples for video: upload with `client.files.upload`, poll `client.files.get` until `state.name == "ACTIVE"`, then call `client.models.generate_content`.
- Batch helpers: `client.batches.create`, `client.batches.get`, `client.batches.cancel/delete`; embedding batch via `client.batches.create_embeddings`.
- MCP integration: `mcpToTool` for Python allows hooking external tools; thought signatures auto-managed when using SDK chat APIs.

### 4.2 JavaScript (`@google/genai`)
- Similar APIs with async iterators for streaming.
- Utility functions (`createPartFromUri`, `mcpToTool`) integrate with Model Context Protocol (MCP).
- Chat & streaming: `const chat = ai.chats.create({...}); const stream = await chat.sendMessageStream(...)` with `for await` to handle partial outputs.
- File upload: `ai.files.upload({ file, config: { mimeType }})` with Node streams; wait for `state` updates via `ai.files.get`.
- Supports automatic function calling when mapping functions; Live API accessible from browser contexts (subject to permission).

### 4.3 Go (`cloud.google.com/go/genai`)
- Governed by context-based client calls; strongly typed options for embedding requests, function calling, streaming.
- Streaming example: `stream := client.Models.GenerateContentStream(ctx, model, genai.Text(prompt), nil)`; iterate through channel for partial responses.
- Files API: `client.Files.UploadFromPath`, `client.Files.Get`, `client.Files.Delete`.
- Embeddings: `client.Models.EmbedContent` returning typed vectors; specify `TaskType` and `OutputDimensionality`.

### 4.4 REST / curl
- Use `:generateContent`, `:countTokens`, `:batchGenerateContent`, Files API endpoints.
- Authenticate via `x-goog-api-key` (Developer API) or OAuth (Vertex AI).
- Inline video/audio/images require base64 encoding; YouTube ingestion uses `file_data.file_uri`.
- SSE streaming endpoint: `models/{model}:streamGenerateContent?alt=sse` for incremental JSON events.
- Batch creation via `models/{model}:batchGenerateContent`; inspect responses with `jq`.
- Files API resumable uploads require `X-Goog-Upload-Command` sequences (`start`, `upload, finalize`).

### 4.5 Live API
- WebSocket-based; supports multimodal back-and-forth with streaming tokens, audio capture, camera frames.
- Pricing distinct from standard calls (see §3).
- Session limits enforced (e.g., 3 sessions on free tier).
- Provides `Advanced Voice Mode` (mobile) with live video streaming; quotas reset daily per session limit.
- Supports tool use, search grounding, and thinking budgets during live interactions.
- Best used for conversational agents requiring low latency audio/video exchange.

---

## 5. Prompt & Workflow Tips

- **Be explicit:** Describe desired format, include anchor examples (few-shot), specify timestamp/timezone formats, call out math/LaTeX requirements.
- **Break tasks:** For complex flows, chain prompts (analysis → synthesis) or orchestrate via function calling.
- **Use schemas:** `response_schema` or JSON schema ensures predictable outputs for downstream parsing.
- **Monitor safety ratings:** Responses may include `safety_ratings`; handle gracefully (retry with adjusted content or escalate).
- **Logging:** Capture request/response metadata (token usage, rate limits) to tune batch sizes and throttling.
- **Error handling:**
  - `INVALID_ARGUMENT` for exceeding limits → reduce size, check MIME.
  - `FAILED_PRECONDITION` when file still processing → add polling/backoff.
  - `RATE_LIMIT_EXCEEDED` → apply exponential backoff, respect per-model quotas.
  - `SAFETY` → adjust prompt, lower FPS, or remove flagged content.
- **Video prompt tips:** Request both transcripts and visual descriptions; mention desired FPS, clip intervals, or timestamps; clarify whether to summarize audio, visuals, or both.
- **Document prompt tips:** Files API recommended for >20 MB PDFs; Gemini handles up to ~1 000 pages; for inline base64 ensure total <20 MB. Specify if tables should convert to Markdown/JSON.
- **Few-shot strategy:** Provide concise, positive examples of desired behavior (avoid anti-patterns). Keep formatting identical across examples (`Text: …` / `Answer: …`).
- **Schema adherence:** When using enums or JSON output, enforce `propertyOrdering` and ensure examples follow same order to avoid rambling output.
- **Task decomposition:** Split multi-step workflows (e.g., analyze → summarize → quiz). Use function calling or sequential prompts for clarity.
- **Token budgeting:** Use `models.count_tokens` to preflight prompts; adjust segment sizes or caching strategy if approaching limits.

---

## 6. Grounding & Contextual Augmentation

- **Grounding with Google Search:** Add `tools=[{"google_search":{}}]` to bring in up-to-date facts. Charges apply after free quota.
- **Context caching:** Cache long documents/videos for multiple passes (e.g., transcript + Q&A). Useful when iterating on summarization style.
- **MCP integration:** Both Python/JS SDKs can connect to MCP servers for external data sources (databases, internal APIs) via tools.
- **Batch retrieval:** Combine embeddings with vector DB (BigQuery Vector, AlloyDB, Cloud SQL, Pinecone, Weaviate, Qdrant) for RAG pipelines; use Matryoshka dims to balance cost vs accuracy.
- **Dynamic retrieval:** When using Search grounding, only requests that return at least one support URL incur charges beyond free quota.
- **Tool chaining:** Gemini thinking models work with search, code execution, structured output, and function calling simultaneously to build agents.

---

## 7. Documentation Quick Links

- Video Understanding: `ai.google.dev/gemini-api/docs/video-understanding`
- Text Generation: `ai.google.dev/gemini-api/docs/text-generation`
- Structured Output: `ai.google.dev/gemini-api/docs/structured-output`
- Thinking: `ai.google.dev/gemini-api/docs/thinking`
- Document Understanding: `ai.google.dev/gemini-api/docs/document-processing`
- Image/Audio Understanding: `ai.google.dev/gemini-api/docs/image-understanding`, `/audio`
- Function Calling: `ai.google.dev/gemini-api/docs/function-calling`
- Batch API: `ai.google.dev/gemini-api/docs/batch-api`
- Context Caching: `ai.google.dev/gemini-api/docs/caching`
- Files API: `ai.google.dev/gemini-api/docs/files`
- Tokens: `ai.google.dev/gemini-api/docs/tokens`
- Prompting Strategies: `ai.google.dev/gemini-api/docs/prompting-strategies`
- Rate Limits: `ai.google.dev/gemini-api/docs/rate-limits`
- Embeddings: `ai.google.dev/gemini-api/docs/embeddings`
- Pricing: `ai.google.dev/gemini-api/docs/pricing`

---

### Summary
The Gemini platform delivers multimodal generation, understanding, and agentic capabilities with consistent tooling across REST, Python, JavaScript, and Go. Video and audio ingestion rely on the Files API and fine-grained metadata controls; structured output, thinking budgets, and function calling make it suitable for complex orchestration. Costs, rate limits, and batching differ by model family, so select the tier/model that balances latency, accuracy, and budget. Continually monitor preview models, pricing updates, and quota changes to keep solutions aligned with Google’s latest releases.

---

## Appendix: Code Snippet Library from Gemini API Docs

### A. Video Understanding

#### A.1 Upload a video file (Python)
```python
from google import genai

client = genai.Client()

myfile = client.files.upload(file="path/to/sample.mp4")

response = client.models.generate_content(
    model="gemini-2.5-flash", contents=[myfile, "Summarize this video. Then create a quiz with an answer key based on the information in this video."]
)

print(response.text)
```

#### A.2 Upload a video file (JavaScript)
```javascript
import {
  GoogleGenAI,
  createUserContent,
  createPartFromUri,
} from "@google/genai";

const ai = new GoogleGenAI({});

async function main() {
  const myfile = await ai.files.upload({
    file: "path/to/sample.mp4",
    config: { mimeType: "video/mp4" },
  });

  const response = await ai.models.generateContent({
    model: "gemini-2.5-flash",
    contents: createUserContent([
      createPartFromUri(myfile.uri, myfile.mimeType),
      "Summarize this video. Then create a quiz with an answer key based on the information in this video.",
    ]),
  });
  console.log(response.text);
}

await main();
```

#### A.3 Upload a video file (Go)
```go
uploadedFile, _ := client.Files.UploadFromPath(ctx, "path/to/sample.mp4", nil)

parts := []*genai.Part{
    genai.NewPartFromText("Summarize this video. Then create a quiz with an answer key based on the information in this video."),
    genai.NewPartFromURI(uploadedFile.URI, uploadedFile.MIMEType),
}

contents := []*genai.Content{
    genai.NewContentFromParts(parts, genai.RoleUser),
}

result, _ := client.Models.GenerateContent(
    ctx,
    "gemini-2.5-flash",
    contents,
    nil,
)

fmt.Println(result.Text())
```

#### A.4 Upload a video file (REST/curl)
```bash
VIDEO_PATH="path/to/sample.mp4"
MIME_TYPE=$(file -b --mime-type "${VIDEO_PATH}")
NUM_BYTES=$(wc -c < "${VIDEO_PATH}")
DISPLAY_NAME=VIDEO

tmp_header_file=upload-header.tmp

echo "Starting file upload..."
curl "https://generativelanguage.googleapis.com/upload/v1beta/files" \
  -H "x-goog-api-key: $GEMINI_API_KEY" \
  -D ${tmp_header_file} \
  -H "X-Goog-Upload-Protocol: resumable" \
  -H "X-Goog-Upload-Command: start" \
  -H "X-Goog-Upload-Header-Content-Length: ${NUM_BYTES}" \
  -H "X-Goog-Upload-Header-Content-Type: ${MIME_TYPE}" \
  -H "Content-Type: application/json" \
  -d "{'file': {'display_name': '${DISPLAY_NAME}'}}" 2> /dev/null

upload_url=$(grep -i "x-goog-upload-url: " "${tmp_header_file}" | cut -d" " -f2 | tr -d "\r")
rm "${tmp_header_file}"

echo "Uploading video data..."
curl "${upload_url}" \
  -H "Content-Length: ${NUM_BYTES}" \
  -H "X-Goog-Upload-Offset: 0" \
  -H "X-Goog-Upload-Command: upload, finalize" \
  --data-binary "@${VIDEO_PATH}" 2> /dev/null > file_info.json

file_uri=$(jq -r ".file.uri" file_info.json)
echo file_uri=$file_uri

echo "File uploaded successfully. File URI: ${file_uri}"

# --- 3. Generate content using the uploaded video file ---
echo "Generating content from video..."
curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent" \
    -H "x-goog-api-key: $GEMINI_API_KEY" \
    -H 'Content-Type: application/json' \
    -X POST \
    -d '{
      "contents": [{
        "parts":[
          {"file_data":{"mime_type": "'"${MIME_TYPE}"'", "file_uri": "'"${file_uri}"'"}},
          {"text": "Summarize this video. Then create a quiz with an answer key based on the information in this video."}]
        }]
      }' 2> /dev/null > response.json

jq -r ".candidates[].content.parts[].text" response.json
```

#### A.5 Inline video data (Python)
```python
from google import genai
from google.genai import types

# Only for videos of size <20Mb
video_file_name = "/path/to/your/video.mp4"
video_bytes = open(video_file_name, 'rb').read()

client = genai.Client()
response = client.models.generate_content(
    model='models/gemini-2.5-flash',
    contents=types.Content(
        parts=[
            types.Part(
                inline_data=types.Blob(data=video_bytes, mime_type='video/mp4')
            ),
            types.Part(text='Please summarize the video in 3 sentences.')
        ]
    )
)
```

#### A.6 Inline video data (JavaScript)
```javascript
import { GoogleGenAI } from "@google/genai";
import * as fs from "node:fs";

const ai = new GoogleGenAI({});
const base64VideoFile = fs.readFileSync("path/to/small-sample.mp4", {
  encoding: "base64",
});

const contents = [
  {
    inlineData: {
      mimeType: "video/mp4",
      data: base64VideoFile,
    },
  },
  { text: "Please summarize the video in 3 sentences." }
];

const response = await ai.models.generateContent({
  model: "gemini-2.5-flash",
  contents: contents,
});
console.log(response.text);
```

#### A.7 Inline video data (REST/curl)
```bash
VIDEO_PATH=/path/to/your/video.mp4

if [[ "$(base64 --version 2>&1)" = *"FreeBSD"* ]]; then
  B64FLAGS="--input"
else
  B64FLAGS="-w0"
fi

curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent" \
    -H "x-goog-api-key: $GEMINI_API_KEY" \
    -H 'Content-Type: application/json' \
    -X POST \
    -d '{
      "contents": [{
        "parts":[
            {
              "inline_data": {
                "mime_type":"video/mp4",
                "data": "'$(base64 $B64FLAGS $VIDEO_PATH)'"
              }
            },
            {"text": "Please summarize the video in 3 sentences."}
        ]
      }]
    }' 2> /dev/null
```

#### A.8 YouTube URL ingestion (Python)
```python
response = client.models.generate_content(
    model='models/gemini-2.5-flash',
    contents=types.Content(
        parts=[
            types.Part(
                file_data=types.FileData(file_uri='https://www.youtube.com/watch?v=9hE5-98ZeCg')
            ),
            types.Part(text='Please summarize the video in 3 sentences.')
        ]
    )
)
```

#### A.9 YouTube URL ingestion (JavaScript)
```javascript
import { GoogleGenerativeAI } from "@google/generative-ai";

const genAI = new GoogleGenerativeAI(process.env.GOOGLE_API_KEY);
const model = genAI.getGenerativeModel({ model: "gemini-2.5-flash" });
const result = await model.generateContent([
  "Please summarize the video in 3 sentences.",
  {
    fileData: {
      fileUri: "https://www.youtube.com/watch?v=9hE5-98ZeCg",
    },
  },
]);
console.log(result.response.text());
```

#### A.10 YouTube URL ingestion (Go)
```go
parts := []*genai.Part{
    genai.NewPartFromText("Please summarize the video in 3 sentences."),
    genai.NewPartFromURI("https://www.youtube.com/watch?v=9hE5-98ZeCg","video/mp4"),
}

contents := []*genai.Content{
    genai.NewContentFromParts(parts, genai.RoleUser),
}

result, _ := client.Models.GenerateContent(
    ctx,
    "gemini-2.5-flash",
    contents,
    nil,
)

fmt.Println(result.Text())
```

#### A.11 YouTube URL ingestion (REST/curl)
```bash
curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent" \
    -H "x-goog-api-key: $GEMINI_API_KEY" \
    -H 'Content-Type: application/json' \
    -X POST \
    -d '{
      "contents": [{
        "parts":[
            {"text": "Please summarize the video in 3 sentences."},
            {
              "file_data": {
                "file_uri": "https://www.youtube.com/watch?v=9hE5-98ZeCg"
              }
            }
        ]
      }]
    }' 2> /dev/null
```

#### A.12 Refer to timestamps (Python)
```python
prompt = "What are the examples given at 00:05 and 00:10 supposed to show us?" # Adjusted timestamps for the NASA video
```

#### A.13 Refer to timestamps (JavaScript)
```javascript
const prompt = "What are the examples given at 00:05 and 00:10 supposed to show us?";
```

#### A.14 Refer to timestamps (Go)
```go
    prompt := []*genai.Part{
        genai.NewPartFromURI(currentVideoFile.URI, currentVideoFile.MIMEType),
         // Adjusted timestamps for the NASA video
        genai.NewPartFromText("What are the examples given at 00:05 and " +
            "00:10 supposed to show us?"),
    }
```

#### A.15 Refer to timestamps (REST/curl)
```bash
PROMPT="What are the examples given at 00:05 and 00:10 supposed to show us?"
```

#### A.16 Transcribe and provide visual descriptions (Python prompt)
```python
prompt = "Transcribe the audio from this video, giving timestamps for salient events in the video. Also provide visual descriptions."
```

#### A.17 Transcribe and provide visual descriptions (JavaScript prompt)
```javascript
const prompt = "Transcribe the audio from this video, giving timestamps for salient events in the video. Also provide visual descriptions.";
```

#### A.18 Transcribe and provide visual descriptions (Go prompt)
```go
    prompt := []*genai.Part{
        genai.NewPartFromURI(currentVideoFile.URI, currentVideoFile.MIMEType),
        genai.NewPartFromText("Transcribe the audio from this video, giving timestamps for salient events in the video. Also " +
            "provide visual descriptions."),
    }
```

#### A.19 Transcribe and provide visual descriptions (REST prompt)
```bash
PROMPT="Transcribe the audio from this video, giving timestamps for salient events in the video. Also provide visual descriptions."
```

#### A.20 Clipping intervals (Python)
```python
from google import genai
from google.genai import types

client = genai.Client()
response = client.models.generate_content(
    model='models/gemini-2.5-flash',
    contents=types.Content(
        parts=[
            types.Part(
                file_data=types.FileData(file_uri='https://www.youtube.com/watch?v=XEzRZ35urlk'),
                video_metadata=types.VideoMetadata(
                    start_offset='1250s',
                    end_offset='1570s'
                )
            ),
            types.Part(text='Please summarize the video in 3 sentences.')
        ]
    )
)
```

#### A.21 Clipping intervals (JavaScript)
```javascript
import { GoogleGenAI } from '@google/genai';
const ai = new GoogleGenAI({});
const model = 'gemini-2.5-flash';

async function main() {
const contents = [
  {
    role: 'user',
    parts: [
      {
        fileData: {
          fileUri: 'https://www.youtube.com/watch?v=9hE5-98ZeCg',
          mimeType: 'video/*',
        },
        videoMetadata: {
          startOffset: '40s',
          endOffset: '80s',
        }
      },
      {
        text: 'Please summarize the video in 3 sentences.',
      },
    ],
  },
];

const response = await ai.models.generateContent({
  model,
  contents,
});

console.log(response.text)

}

await main();
```

#### A.22 Custom frame rate (Python)
```python
from google import genai
from google.genai import types

# Only for videos of size <20Mb
video_file_name = "/path/to/your/video.mp4"
video_bytes = open(video_file_name, 'rb').read()

client = genai.Client()
response = client.models.generate_content(
    model='models/gemini-2.5-flash',
    contents=types.Content(
        parts=[
            types.Part(
                inline_data=types.Blob(
                    data=video_bytes,
                    mime_type='video/mp4'),
                video_metadata=types.VideoMetadata(fps=5)
            ),
            types.Part(text='Please summarize the video in 3 sentences.')
        ]
    )
)
```

### B. Text Generation

#### B.1 Basic text generation (Python)
```python
from google import genai

client = genai.Client()

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="How does AI work?"
)
print(response.text)
```

#### B.2 Basic text generation (JavaScript)
```javascript
import { GoogleGenAI } from "@google/genai";

const ai = new GoogleGenAI({});

async function main() {
  const response = await ai.models.generateContent({
    model: "gemini-2.5-flash",
    contents: "How does AI work?",
  });
  console.log(response.text);
}

await main();
```

#### B.3 Basic text generation (Go)
```go
package main

import (
  "context"
  "fmt"
  "os"
  "google.golang.org/genai"
)

func main() {

  ctx := context.Background()
  client, err := genai.NewClient(ctx, nil)
  if err != nil {
      log.Fatal(err)
  }

  result, _ := client.Models.GenerateContent(
      ctx,
      "gemini-2.5-flash",
      genai.Text("Explain how AI works in a few words"),
      nil,
  )

  fmt.Println(result.Text())
}
```

#### B.4 Basic text generation (REST/curl)
```bash
curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent" \
  -H "x-goog-api-key: $GEMINI_API_KEY" \
  -H 'Content-Type: application/json' \
  -X POST \
  -d '{
    "contents": [
      {
        "parts": [
          {
            "text": "How does AI work?"
          }
        ]
      }
    ]
  }'
```

#### B.5 Basic text generation (Apps Script)
```javascript
// See https://developers.google.com/apps-script/guides/properties
// for instructions on how to set the API key.
const apiKey = PropertiesService.getScriptProperties().getProperty('GEMINI_API_KEY');

function main() {
  const payload = {
    contents: [
      {
        parts: [
          { text: 'How AI does work?' },
        ],
      },
    ],
  };

  const url = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent';
  const options = {
    method: 'POST',
    contentType: 'application/json',
    headers: {
      'x-goog-api-key': apiKey,
    },
    payload: JSON.stringify(payload)
  };

  const response = UrlFetchApp.fetch(url, options);
  const data = JSON.parse(response);
  const content = data['candidates'][0]['content']['parts'][0]['text'];
  console.log(content);
}
```

#### B.6 Thinking budget example (Python)
```python
from google import genai
from google.genai import types

client = genai.Client()

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="How does AI work?",
    config=types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_budget=0) # Disables thinking
    ),
)
print(response.text)
```

#### B.7 Thinking budget example (JavaScript)
```javascript
import { GoogleGenAI } from "@google.genai";

const ai = new GoogleGenAI({});

async function main() {
  const response = await ai.models.generateContent({
    model: "gemini-2.5-flash",
    contents: "How does AI work?",
    config: {
      thinkingConfig: {
        thinkingBudget: 0, // Disables thinking
      },
    }
  });
  console.log(response.text);
}

await main();
```

#### B.8 Thinking budget example (Go)
```go
package main

import (
  "context"
  "fmt"
  "os"
  "google.golang.org/genai"
)

func main() {

  ctx := context.Background()
  client, err := genai.NewClient(ctx, nil)
  if err != nil {
      log.Fatal(err)
  }

  result, _ := client.Models.GenerateContent(
      ctx,
      "gemini-2.5-flash",
      genai.Text("How does AI work?"),
      &genai.GenerateContentConfig{
        ThinkingConfig: &genai.ThinkingConfig{
            ThinkingBudget: int32(0), // Disables thinking
        },
      }
  )

  fmt.Println(result.Text())
}
```

#### B.9 Thinking budget example (REST/curl)
```bash
curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent" \
  -H "x-goog-api-key: $GEMINI_API_KEY" \
  -H 'Content-Type: application/json' \
  -X POST \
  -d '{
    "contents": [
      {
        "parts": [
          {
            "text": "How does AI work?"
          }
        ]
      }
    ],
    "generationConfig": {
      "thinkingConfig": {
        "thinkingBudget": 0
      }
    }
  }'
```

#### B.10 System instructions (Python)
```python
from google import genai
from google.genai import types

client = genai.Client()

response = client.models.generate_content(
    model="gemini-2.5-flash",
    config=types.GenerateContentConfig(
        system_instruction="You are a cat. Your name is Neko."),
    contents="Hello there"
)

print(response.text)
```

#### B.11 System instructions (JavaScript)
```javascript
import { GoogleGenAI } from "@google/genai";

const ai = new GoogleGenAI({});

async function main() {
  const response = await ai.models.generateContent({
    model: "gemini-2.5-flash",
    contents: "Hello there",
    config: {
      systemInstruction: "You are a cat. Your name is Neko.",
    },
  });
  console.log(response.text);
}

await main();
```

#### B.12 System instructions (Go)
```go
package main

import (
  "context"
  "fmt"
  "os"
  "google.golang.org/genai"
)

func main() {

  ctx := context.Background()
  client, err := genai.NewClient(ctx, nil)
  if err != nil {
      log.Fatal(err)
  }

  config := &genai.GenerateContentConfig{
      SystemInstruction: genai.NewContentFromText("You are a cat. Your name is Neko.", genai.RoleUser),
  }

  result, _ := client.Models.GenerateContent(
      ctx,
      "gemini-2.5-flash",
      genai.Text("Hello there"),
      config,
  )

  fmt.Println(result.Text())
}
```

#### B.13 System instructions (REST/curl)
```bash
curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent" \
  -H "x-goog-api-key: $GEMINI_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "system_instruction": {
      "parts": [
        {
          "text": "You are a cat. Your name is Neko."
        }
      ]
    },
    "contents": [
      {
        "parts": [
          {
            "text": "Hello there"
          }
        ]
      }
    ]
  }'
```

#### B.14 Generation config (Python)
```python
from google import genai
from google.genai import types

client = genai.Client()

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents=["Explain how AI works"],
    config=types.GenerateContentConfig(
        temperature=0.1
    )
)
print(response.text)
```

#### B.15 Generation config (JavaScript)
```javascript
import { GoogleGenAI } from "@google/genai";

const ai = new GoogleGenAI({});

async function main() {
  const response = await ai.models.generateContent({
    model: "gemini-2.5-flash",
    contents: "Explain how AI works",
    config: {
      temperature: 0.1,
    },
  });
  console.log(response.text);
}

await main();
```

#### B.16 Generation config (Go)
```go
package main

import (
  "context"
  "fmt"
  "log"
  "google.golang.org/genai"
)

func main() {

  ctx := context.Background()
  client, err := genai.NewClient(ctx, nil)
  if err != nil {
      log.Fatal(err)
  }

  temp := float32(0.9)
  topP := float32(0.5)
  topK := float32(20.0)

  config := &genai.GenerateContentConfig{
    Temperature:       &temp,
    TopP:              &topP,
    TopK:              &topK,
    ResponseMIMEType:  "application/json",
  }

  result, _ := client.Models.GenerateContent(
    ctx,
    "gemini-2.5-flash",
    genai.Text("What is the average size of a swallow?"),
    config,
  )

  fmt.Println(result.Text())
}
```

### C. Structured Output

#### C.1 Generating JSON (Python)
```python
from google import genai
from pydantic import BaseModel

class Recipe(BaseModel):
    recipe_name: str
    ingredients: list[str]

client = genai.Client()
response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="List a few popular cookie recipes, and include the amounts of ingredients.",
    config={
        "response_mime_type": "application/json",
        "response_schema": list[Recipe],
    },
)
# Use the response as a JSON string.
print(response.text)

# Use instantiated objects.
my_recipes: list[Recipe] = response.parsed
```

#### C.2 Generating JSON (JavaScript)
```javascript
import { GoogleGenAI, Type } from "@google/genai";

const ai = new GoogleGenAI({});

async function main() {
  const response = await ai.models.generateContent({
    model: "gemini-2.5-flash",
    contents:
      "List a few popular cookie recipes, and include the amounts of ingredients.",
    config: {
      responseMimeType: "application/json",
      responseSchema: {
        type: Type.ARRAY,
        items: {
          type: Type.OBJECT,
          properties: {
            recipeName: {
              type: Type.STRING,
            },
            ingredients: {
              type: Type.ARRAY,
              items: {
                type: Type.STRING,
              },
            },
          },
          propertyOrdering: ["recipeName", "ingredients"],
        },
      },
    },
  });

  console.log(response.text);
}

main();
```

#### C.3 Generating JSON (Go)
```go
package main

import (
    "context"
    "fmt"
    "log"

    "google.golang.org/genai"
)

func main() {
    ctx := context.Background()
    client, err := genai.NewClient(ctx, nil)
    if err != nil {
        log.Fatal(err)
    }

    config := &genai.GenerateContentConfig{
        ResponseMIMEType: "application/json",
        ResponseSchema: &genai.Schema{
            Type: genai.TypeArray,
            Items: &genai.Schema{
                Type: genai.TypeObject,
                Properties: map[string]*genai.Schema{
                    "recipeName": {Type: genai.TypeString},
                    "ingredients": {
                        Type:  genai.TypeArray,
                        Items: &genai.Schema{Type: genai.TypeString},
                    },
                },
                PropertyOrdering: []string{"recipeName", "ingredients"},
            },
        },
    }

    result, err := client.Models.GenerateContent(
        ctx,
        "gemini-2.5-flash",
        genai.Text("List a few popular cookie recipes, and include the amounts of ingredients."),
        config,
    )
    if err != nil {
        log.Fatal(err)
    }
    fmt.Println(result.Text())
}
```

#### C.4 Generating JSON (REST/curl)
```bash
curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent" \
-H "x-goog-api-key: $GEMINI_API_KEY" \
-H 'Content-Type: application/json' \
-d '{
      "contents": [{
        "parts":[
          { "text": "List a few popular cookie recipes, and include the amounts of ingredients." }
        ]
      }],
      "generationConfig": {
        "responseMimeType": "application/json",
        "responseSchema": {
          "type": "ARRAY",
          "items": {
            "type": "OBJECT",
            "properties": {
              "recipeName": { "type": "STRING" },
              "ingredients": {
                "type": "ARRAY",
                "items": { "type": "STRING" }
              }
            },
            "propertyOrdering": ["recipeName", "ingredients"]
          }
        }
      }
}' 2> /dev/null | head
```

#### C.5 Generating enum values (Python)
```python
from google import genai
import enum

class Instrument(enum.Enum):
  PERCUSSION = "Percussion"
  STRING = "String"
  WOODWIND = "Woodwind"
  BRASS = "Brass"
  KEYBOARD = "Keyboard"

client = genai.Client()
response = client.models.generate_content(
    model='gemini-2.5-flash',
    contents='What type of instrument is an oboe?',
    config={
        'response_mime_type': 'text/x.enum',
        'response_schema': Instrument,
    },
)

print(response.text)
# Woodwind
```

#### C.6 Generating enum values (JavaScript)
```javascript
import { GoogleGenAI, Type } from "@google/genai";

const ai = new GoogleGenAI({});

const response = await ai.models.generateContent({
    model: "gemini-2.5-flash",
    contents: "What type of instrument is an oboe?",
    config: {
      responseMimeType: "text/x.enum",
      responseSchema: {
        type: Type.STRING,
        enum: ["Percussion", "String", "Woodwind", "Brass", "Keyboard"],
      },
    },
  });

console.log(response.text);
```

#### C.7 Generating enum values (REST/curl)
```bash
curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent" \
-H "x-goog-api-key: $GEMINI_API_KEY" \
    -H 'Content-Type: application/json' \
    -d '{
          "contents": [{
            "parts":[
              { "text": "What type of instrument is an oboe?" }
            ]
          }],
          "generationConfig": {
            "responseMimeType": "text/x.enum",
            "responseSchema": {
              "type": "STRING",
              "enum": ["Percussion", "String", "Woodwind", "Brass", "Keyboard"]
            }
          }
    }'
```

### D. Streaming Responses

#### D.1 Streaming (Python)
```python
from google import genai

client = genai.Client()

response = client.models.generate_content_stream(
    model="gemini-2.5-flash",
    contents=["Explain how AI works"]
)
for chunk in response:
    print(chunk.text, end="")
```

#### D.2 Streaming (JavaScript)
```javascript
import { GoogleGenAI } from "@google/genai";

const ai = new GoogleGenAI({});

async function main() {
  const response = await ai.models.generateContentStream({
    model: "gemini-2.5-flash",
    contents: "Explain how AI works",
  });

  for await (const chunk of response) {
    console.log(chunk.text);
  }
}

await main();
```

#### D.3 Streaming (Go)
```go
package main

import (
  "context"
  "fmt"
  "log"
  "google.golang.org/genai"
)

func main() {

  ctx := context.Background()
  client, err := genai.NewClient(ctx, nil)
  if err != nil {
      log.Fatal(err)
  }

  stream := client.Models.GenerateContentStream(
      ctx,
      "gemini-2.5-flash",
      genai.Text("Write a story about a magic backpack."),
      nil,
  )

  for chunk, ok := <-stream; ok; chunk, ok = <-stream {
      part := chunk.Candidates[0].Content.Parts[0]
      fmt.Print(part.Text)
  }
}
```

#### D.4 Streaming (REST/curl SSE)
```bash
curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:streamGenerateContent?alt=sse" \
  -H "x-goog-api-key: $GEMINI_API_KEY" \
  -H 'Content-Type: application/json' \
  --no-buffer \
  -d '{
    "contents": [
      {
        "parts": [
          {
            "text": "Explain how AI works"
          }
        ]
      }
    ]
  }'
```

### E. Multi-turn Conversations

#### E.1 Chat (Python)
```python
from google import genai

client = genai.Client()
chat = client.chats.create(model="gemini-2.5-flash")

response = chat.send_message("I have 2 dogs in my house.")
print(response.text)

response = chat.send_message("How many paws are in my house?")
print(response.text)

for message in chat.get_history():
    print(f'role - {message.role}',end=": ")
    print(message.parts[0].text)
```

#### E.2 Chat (JavaScript)
```javascript
import { GoogleGenAI } from "@google/genai";

const ai = new GoogleGenAI({});

async function main() {
  const chat = ai.chats.create({
    model: "gemini-2.5-flash",
    history: [
      {
        role: "user",
        parts: [{ text: "Hello" }],
      },
      {
        role: "model",
        parts: [{ text: "Great to meet you. What would you like to know?" }],
      },
    ],
  });

  const response1 = await chat.sendMessage({
    message: "I have 2 dogs in my house.",
  });
  console.log("Chat response 1:", response1.text);

  const response2 = await chat.sendMessage({
    message: "How many paws are in my house?",
  });
  console.log("Chat response 2:", response2.text);
}

await main();
```

#### E.3 Chat (Go)
```go
package main

import (
  "context"
  "fmt"
  "log"
  "google.golang.org/genai"
)

func main() {

  ctx := context.Background()
  client, err := genai.NewClient(ctx, nil)
  if err != nil {
      log.Fatal(err)
  }

  history := []*genai.Content{
      genai.NewContentFromText("Hi nice to meet you! I have 2 dogs in my house.", genai.RoleUser),
      genai.NewContentFromText("Great to meet you. What would you like to know?", genai.RoleModel),
  }

  chat, _ := client.Chats.Create(ctx, "gemini-2.5-flash", nil, history)
  res, _ := chat.SendMessage(ctx, genai.Part{Text: "How many paws are in my house?"})

  if len(res.Candidates) > 0 {
      fmt.Println(res.Candidates[0].Content.Parts[0].Text)
  }
}
```

#### E.4 Chat (REST/curl)
```bash
curl https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent \
  -H "x-goog-api-key: $GEMINI_API_KEY" \
  -H 'Content-Type: application/json' \
  -X POST \
  -d '{
    "contents": [
      {
        "role": "user",
        "parts": [
          {
            "text": "Hello"
          }
        ]
      },
      {
        "role": "model",
        "parts": [
          {
            "text": "Great to meet you. What would you like to know?"
          }
        ]
      },
      {
        "role": "user",
        "parts": [
          {
            "text": "I have two dogs in my house. How many paws are in my house?"
          }
        ]
      }
    ]
  }'
```

#### E.5 Chat (Apps Script)
```javascript
// See https://developers.google.com/apps-script/guides/properties
// for instructions on how to set the API key.
const apiKey = PropertiesService.getScriptProperties().getProperty('GEMINI_API_KEY');

function main() {
  const payload = {
    contents: [
      {
        role: 'user',
        parts: [
          { text: 'Hello' },
        ],
      },
      {
        role: 'model',
        parts: [
          { text: 'Great to meet you. What would you like to know?' },
        ],
      },
      {
        role: 'user',
        parts: [
          { text: 'I have two dogs in my house. How many paws are in my house?' },
        ],
      },
    ],
  };

  const url = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent';
  const options = {
    method: 'POST',
    contentType: 'application/json',
    headers: {
      'x-goog-api-key': apiKey,
    },
    payload: JSON.stringify(payload)
  };

  const response = UrlFetchApp.fetch(url, options);
  const data = JSON.parse(response);
  const content = data['candidates'][0]['content']['parts'][0]['text'];
  console.log(content);
}
```

#### E.6 Streaming chat (Python)
```python
from google import genai

client = genai.Client()
chat = client.chats.create(model="gemini-2.5-flash")

response = chat.send_message_stream("I have 2 dogs in my house.")
for chunk in response:
    print(chunk.text, end="")

response = chat.send_message_stream("How many paws are in my house?")
for chunk in response:
    print(chunk.text, end="")

for message in chat.get_history():
    print(f'role - {message.role}', end=": ")
    print(message.parts[0].text)
```

#### E.7 Streaming chat (JavaScript)
```javascript
import { GoogleGenAI } from "@google/genai";

const ai = new GoogleGenAI({});

async function main() {
  const chat = ai.chats.create({
    model: "gemini-2.5-flash",
    history: [
      {
        role: "user",
        parts: [{ text: "Hello" }],
      },
      {
        role: "model",
        parts: [{ text: "Great to meet you. What would you like to know?" }],
      },
    ],
  });

  const stream1 = await chat.sendMessageStream({
    message: "I have 2 dogs in my house.",
  });
  for await (const chunk of stream1) {
    console.log(chunk.text);
    console.log("_".repeat(80));
  }

  const stream2 = await chat.sendMessageStream({
    message: "How many paws are in my house?",
  });
  for await (const chunk of stream2) {
    console.log(chunk.text);
    console.log("_".repeat(80));
  }
}

await main();
```

#### E.8 Streaming chat (Go)
```go
package main

import (
  "context"
  "fmt"
  "log"
  "google.golang.org/genai"
)

func main() {

  ctx := context.Background()
  client, err := genai.NewClient(ctx, nil)
  if err != nil {
      log.Fatal(err)
  }

  history := []*genai.Content{
      genai.NewContentFromText("Hi nice to meet you! I have 2 dogs in my house.", genai.RoleUser),
      genai.NewContentFromText("Great to meet you. What would you like to know?", genai.RoleModel),
  }

  chat, _ := client.Chats.Create(ctx, "gemini-2.5-flash", nil, history)
  stream := chat.SendMessageStream(ctx, genai.Part{Text: "How many paws are in my house?"})

  for chunk := range stream {
      part := chunk.Candidates[0].Content.Parts[0]
      fmt.Print(part.Text)
  }
}
```

### F. Embeddings

#### F.1 Single embedding (Python)
```python
from google import genai

client = genai.Client()

result = client.models.embed_content(
        model="gemini-embedding-001",
        contents="What is the meaning of life?")

print(result.embeddings)
```

#### F.2 Single embedding (JavaScript)
```javascript
import { GoogleGenAI } from "@google/genai";

async function main() {

    const ai = new GoogleGenAI({});

    const response = await ai.models.embedContent({
        model: 'gemini-embedding-001',
        contents: 'What is the meaning of life?',
    });

    console.log(response.embeddings);
}

main();
```

#### F.3 Single embedding (Go)
```go
package main

import (
    "context"
    "encoding/json"
    "fmt"
    "log"

    "google.golang.org/genai"
)

func main() {
    ctx := context.Background()
    client, err := genai.NewClient(ctx, nil)
    if err != nil {
        log.Fatal(err)
    }

    contents := []*genai.Content{
        genai.NewContentFromText("What is the meaning of life?", genai.RoleUser),
    }
    result, err := client.Models.EmbedContent(ctx,
        "gemini-embedding-001",
        contents,
        nil,
    )
    if err != nil {
        log.Fatal(err)
    }

    embeddings, err := json.MarshalIndent(result.Embeddings, "", "  ")
    if err != nil {
        log.Fatal(err)
    }
    fmt.Println(string(embeddings))
}
```

#### F.4 Single embedding (REST/curl)
```bash
curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent" \
-H "x-goog-api-key: $GEMINI_API_KEY" \
-H 'Content-Type: application/json' \
-d '{"model": "models/gemini-embedding-001",
     "content": {"parts":[{"text": "What is the meaning of life?"}]}
    }'
```

#### F.5 Multiple embeddings (Python)
```python
from google import genai

client = genai.Client()

result = client.models.embed_content(
        model="gemini-embedding-001",
        contents= [
            "What is the meaning of life?",
            "What is the purpose of existence?",
            "How do I bake a cake?"
        ])

for embedding in result.embeddings:
    print(embedding)
```

#### F.6 Multiple embeddings (JavaScript)
```javascript
import { GoogleGenAI } from "@google/genai";

async function main() {

    const ai = new GoogleGenAI({});

    const response = await ai.models.embedContent({
        model: 'gemini-embedding-001',
        contents: [
            'What is the meaning of life?',
            'What is the purpose of existence?',
            'How do I bake a cake?'
        ],
    });

    console.log(response.embeddings);
}

main();
```

#### F.7 Multiple embeddings (Go)
```go
package main

import (
    "context"
    "encoding/json"
    "fmt"
    "log"

    "google.golang.org/genai"
)

func main() {
    ctx := context.Background()
    client, err := genai.NewClient(ctx, nil)
    if err != nil {
        log.Fatal(err)
    }

    contents := []*genai.Content{
        genai.NewContentFromText("What is the meaning of life?"),
        genai.NewContentFromText("How does photosynthesis work?"),
        genai.NewContentFromText("Tell me about the history of the internet."),
    }
    result, err := client.Models.EmbedContent(ctx,
        "gemini-embedding-001",
        contents,
        nil,
    )
    if err != nil {
        log.Fatal(err)
    }

    embeddings, err := json.MarshalIndent(result.Embeddings, "", "  ")
    if err != nil {
        log.Fatal(err)
    }
    fmt.Println(string(embeddings))
}
```

#### F.8 Multiple embeddings (REST/curl)
```bash
curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:batchEmbedContents" \
-H "x-goog-api-key: $GEMINI_API_KEY" \
-H 'Content-Type: application/json' \
-d '{"requests": [{
    "model": "models/gemini-embedding-001",
    "content": {
    "parts":[{
        "text": "What is the meaning of life?"}]}, },
    {
    "model": "models/gemini-embedding-001",
    "content": {
    "parts":[{
        "text": "How much wood would a woodchuck chuck?"}]}, },
    {
    "model": "models/gemini-embedding-001",
    "content": {
    "parts":[{
        "text": "How does the brain work?"}]}, }, ]}' 2> /dev/null | grep -C 5 values
```

#### F.9 Task type example with cosine similarity (Python)
```python
from google import genai
from google.genai import types
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

client = genai.Client()

texts = [
    "What is the meaning of life?",
    "What is the purpose of existence?",
    "How do I bake a cake?"]

result = [
    np.array(e.values) for e in client.models.embed_content(
        model="gemini-embedding-001",
        contents=texts,
        config=types.EmbedContentConfig(task_type="SEMANTIC_SIMILARITY")).embeddings
]

embeddings_matrix = np.array(result)
similarity_matrix = cosine_similarity(embeddings_matrix)

for i, text1 in enumerate(texts):
    for j in range(i + 1, len(texts)):
        text2 = texts[j]
        similarity = similarity_matrix[i, j]
        print(f"Similarity between '{text1}' and '{text2}': {similarity:.4f}")
```

#### F.10 Task type example (JavaScript)
```javascript
import { GoogleGenAI } from "@google/genai";
import * as cosineSimilarity from "compute-cosine-similarity";

async function main() {
    const ai = new GoogleGenAI({});

    const texts = [
        "What is the meaning of life?",
        "What is the purpose of existence?",
        "How do I bake a cake?",
    ];

    const response = await ai.models.embedContent({
        model: 'gemini-embedding-001',
        contents: texts,
        taskType: 'SEMANTIC_SIMILARITY'
    });

    const embeddings = response.embeddings.map(e => e.values);

    for (let i = 0; i < texts.length; i++) {
        for (let j = i + 1; j < texts.length; j++) {
            const text1 = texts[i];
            const text2 = texts[j];
            const similarity = cosineSimilarity(embeddings[i], embeddings[j]);
            console.log(`Similarity between '${text1}' and '${text2}': ${similarity.toFixed(4)}`);
        }
    }
}

main();
```

#### F.11 Dimensionality control (Python)
```python
from google import genai
from google.genai import types

client = genai.Client()

result = client.models.embed_content(
    model="gemini-embedding-001",
    contents="What is the meaning of life?",
    config=types.EmbedContentConfig(output_dimensionality=768)
)

[embedding_obj] = result.embeddings
embedding_length = len(embedding_obj.values)

print(f"Length of embedding: {embedding_length}")
```

#### F.12 Dimensionality control (JavaScript)
```javascript
import { GoogleGenAI } from "@google/genai";

async function main() {
    const ai = new GoogleGenAI({});

    const response = await ai.models.embedContent({
        model: 'gemini-embedding-001',
        content: 'What is the meaning of life?',
        outputDimensionality: 768,
    });

    const embeddingLength = response.embedding.values.length;
    console.log(`Length of embedding: ${embeddingLength}`);
}

main();
```

#### F.13 Dimensionality control (Go)
```go
package main

import (
    "context"
    "fmt"
    "log"

    "google.golang.org/genai"
)

func main() {
    ctx := context.Background()
    client, err := genai.NewClient(ctx, nil)
    if err != nil {
        log.Fatal(err)
    }
    defer client.Close()

    contents := []*genai.Content{
        genai.NewContentFromText("What is the meaning of life?", genai.RoleUser),
    }

    result, err := client.Models.EmbedContent(ctx,
        "gemini-embedding-001",
        contents,
        &genai.EmbedContentRequest{OutputDimensionality: 768},
    )
    if err != nil {
        log.Fatal(err)
    }

    embedding := result.Embeddings[0]
    embeddingLength := len(embedding.Values)
    fmt.Printf("Length of embedding: %d\n", embeddingLength)
}
```

#### F.14 Dimensionality control (REST/curl)
```bash
curl -X POST "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent" \
    -H "x-goog-api-key: $GEMINI_API_KEY" \
    -H 'Content-Type: application/json' \
    -d '{
        "content": {"parts":[{ "text": "What is the meaning of life?"}]},
        "output_dimensionality": 768
    }'
```

#### F.15 Normalizing smaller dimensions (Python)
```python
import numpy as np
from numpy.linalg import norm

embedding_values_np = np.array(embedding_obj.values)
normed_embedding = embedding_values_np / np.linalg.norm(embedding_values_np)

print(f"Normed embedding length: {len(normed_embedding)}")
print(f"Norm of normed embedding: {np.linalg.norm(normed_embedding):.6f}")
```

### G. Batch API

#### G.1 Inline requests (Python)
```python
from google import genai

client = genai.Client()

# A list of dictionaries, where each is a GenerateContentRequest
inline_requests = [
    {
        'contents': [{
            'parts': [{'text': 'Tell me a one-sentence joke.'}],
            'role': 'user'
        }]
    },
    {
        'contents': [{
            'parts': [{'text': 'Why is the sky blue?'}],
            'role': 'user'
        }]
    }
]

inline_batch_job = client.batches.create(
    model="models/gemini-2.5-flash",
    src=inline_requests,
    config={
        'display_name': "inlined-requests-job-1",
    },
)

print(f"Created batch job: {inline_batch_job.name}")
```

#### G.2 Inline requests (JavaScript)
```javascript
import {GoogleGenAI} from '@google/genai';
const GEMINI_API_KEY = process.env.GEMINI_API_KEY;

const ai = new GoogleGenAI({apiKey: GEMINI_API_KEY});

const inlinedRequests = [
    {
        contents: [{
            parts: [{text: 'Tell me a one-sentence joke.'}],
            role: 'user'
        }]
    },
    {
        contents: [{
            parts: [{'text': 'Why is the sky blue?'}],
            role: 'user'
        }]
    }
]

const response = await ai.batches.create({
    model: 'gemini-2.5-flash',
    src: inlinedRequests,
    config: {
        displayName: 'inlined-requests-job-1',
    }
});

console.log(response);
```

#### G.3 Inline requests (REST/curl)
```bash
curl https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:batchGenerateContent \
-H "x-goog-api-key: $GEMINI_API_KEY" \
-X POST \
-H "Content-Type:application/json" \
-d '{
    "batch": {
        "display_name": "my-batch-requests",
        "input_config": {
            "requests": {
                "requests": [
                    {
                        "request": {"contents": [{"parts": [{"text": "Describe the process of photosynthesis."}]}]},
                        "metadata": {
                            "key": "request-1"
                        }
                    },
                    {
                        "request": {"contents": [{"parts": [{"text": "Describe the process of photosynthesis."}]}]},
                        "metadata": {
                            "key": "request-2"
                        }
                    }
                ]
            }
        }
    }
}'
```

#### G.4 Create JSONL input (Python)
```python
import json
from google import genai
from google.genai import types

client = genai.Client()

# Create a sample JSONL file
with open("my-batch-requests.jsonl", "w") as f:
    requests = [
        {"key": "request-1", "request": {"contents": [{"parts": [{"text": "Describe the process of photosynthesis."}]}]}},
        {"key": "request-2", "request": {"contents": [{"parts": [{"text": "What are the main ingredients in a Margherita pizza?"}]}]}}
    ]
    for req in requests:
        f.write(json.dumps(req) + "\n")

# Upload the file to the File API
uploaded_file = client.files.upload(
    file='my-batch-requests.jsonl',
    config=types.UploadFileConfig(display_name='my-batch-requests', mime_type='jsonl')
)

print(f"Uploaded file: {uploaded_file.name}")
```

#### G.5 Create JSONL input (JavaScript)
```javascript
import {GoogleGenAI} from '@google/genai';
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from 'url';

const GEMINI_API_KEY = process.env.GEMINI_API_KEY;
const ai = new GoogleGenAI({apiKey: GEMINI_API_KEY});
const fileName = "my-batch-requests.jsonl";

const requests = [
    { "key": "request-1", "request": { "contents": [{ "parts": [{ "text": "Describe the process of photosynthesis." }] }] } },
    { "key": "request-2", "request": { "contents": [{ "parts": [{ "text": "What are the main ingredients in a Margherita pizza?" }] }] } }
];

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const filePath = path.join(__dirname, fileName);

async function writeBatchRequestsToFile(requests, filePath) {
    try {
        const writeStream = fs.createWriteStream(filePath, { flags: 'w' });

        writeStream.on('error', (err) => {
            console.error(`Error writing to file ${filePath}:`, err);
        });

        for (const req of requests) {
            writeStream.write(JSON.stringify(req) + '\n');
        }

        writeStream.end();

        console.log(`Successfully wrote batch requests to ${filePath}`);

    } catch (error) {
        console.error(`An unexpected error occurred:`, error);
    }
}

writeBatchRequestsToFile(requests, filePath);

const uploadedFile = await ai.files.upload({file: 'my-batch-requests.jsonl', config: {
    mimeType: 'jsonl',
}});
console.log(uploadedFile.name);
```

#### G.6 Create JSONL input (REST/curl)
```bash
tmp_batch_input_file=batch_input.tmp
echo -e '{"contents": [{"parts": [{"text": "Describe the process of photosynthesis."}]}], "generationConfig": {"temperature": 0.7}}\n{"contents": [{"parts": [{"text": "What are the main ingredients in a Margherita pizza?"}]}]}' > batch_input.tmp
MIME_TYPE=$(file -b --mime-type "${tmp_batch_input_file}")
NUM_BYTES=$(wc -c < "${tmp_batch_input_file}")
DISPLAY_NAME=BatchInput

tmp_header_file=upload-header.tmp

curl "https://generativelanguage.googleapis.com/upload/v1beta/files" \
  -D upload-header.tmp \
  -H "x-goog-api-key: $GEMINI_API_KEY" \
  -H "X-Goog-Upload-Protocol: resumable" \
  -H "X-Goog-Upload-Command: start" \
  -H "X-Goog-Upload-Header-Content-Length: ${NUM_BYTES}" \
  -H "X-Goog-Upload-Header-Content-Type: ${MIME_TYPE}" \
  -H "Content-Type: application/jsonl" \
  -d "{'file': {'display_name': '${DISPLAY_NAME}'}}" 2> /dev/null

upload_url=$(grep -i "x-goog-upload-url: " "${tmp_header_file}" | cut -d" " -f2 | tr -d "\r")
rm "${tmp_header_file}"

curl "${upload_url}" \
  -H "Content-Length: ${NUM_BYTES}" \
  -H "X-Goog-Upload-Offset: 0" \
  -H "X-Goog-Upload-Command: upload, finalize" \
  --data-binary "@${tmp_batch_input_file}" 2> /dev/null > file_info.json

file_uri=$(jq ".file.uri" file_info.json)
```

#### G.7 Create batch from uploaded file (Python)
```python
from google import genai

# Assumes `uploaded_file` is the file object from the previous step
client = genai.Client()
file_batch_job = client.batches.create(
    model="gemini-2.5-flash",
    src=uploaded_file.name,
    config={
        'display_name': "file-upload-job-1",
    },
)

print(f"Created batch job: {file_batch_job.name}")
```

#### G.8 Create batch from uploaded file (JavaScript)
```javascript
// Assumes `uploadedFile` is the file object from the previous step
const fileBatchJob = await ai.batches.create({
    model: 'gemini-2.5-flash',
    src: uploadedFile.name,
    config: {
        displayName: 'file-upload-job-1',
    }
});

console.log(fileBatchJob);
```

#### G.9 Create batch from uploaded file (REST/curl)
```bash
# Set the File ID taken from the upload response.
BATCH_INPUT_FILE='files/123456'
curl https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:batchGenerateContent -X POST -H "x-goog-api-key: $GEMINI_API_KEY" -H "Content-Type:application/json" -d "{
    'batch': {
        'display_name': 'my-batch-requests',
        'input_config': {
            'file_name': '${BATCH_INPUT_FILE}'
        }
    }
}"
```

#### G.10 Poll batch status (Python)
```python
import time
from google import genai

client = genai.Client()

job_name = "YOUR_BATCH_JOB_NAME"  # (e.g. 'batches/your-batch-id')
batch_job = client.batches.get(name=job_name)

completed_states = set([
    'JOB_STATE_SUCCEEDED',
    'JOB_STATE_FAILED',
    'JOB_STATE_CANCELLED',
    'JOB_STATE_EXPIRED',
])

print(f"Polling status for job: {job_name}")
batch_job = client.batches.get(name=job_name)
while batch_job.state.name not in completed_states:
  print(f"Current state: {batch_job.state.name}")
  time.sleep(30)
  batch_job = client.batches.get(name=job_name)

print(f"Job finished with state: {batch_job.state.name}")
if batch_job.state.name == 'JOB_STATE_FAILED':
    print(f"Error: {batch_job.error}")
```

#### G.11 Poll batch status (JavaScript)
```javascript
let batchJob;
const completedStates = new Set([
    'JOB_STATE_SUCCEEDED',
    'JOB_STATE_FAILED',
    'JOB_STATE_CANCELLED',
    'JOB_STATE_EXPIRED',
]);

try {
    batchJob = await ai.batches.get({name: inlinedBatchJob.name});
    while (!completedStates.has(batchJob.state)) {
        console.log(`Current state: ${batchJob.state}`);
        await new Promise(resolve => setTimeout(resolve, 30000));
        batchJob = await client.batches.get({ name: batchJob.name });
    }
    console.log(`Job finished with state: ${batchJob.state}`);
    if (batchJob.state === 'JOB_STATE_FAILED') {
        console.error(`Error: ${batchJob.state}`);
    }
} catch (error) {
    console.error(`An error occurred while polling job ${batchJob.name}:`, error);
}
```

#### G.12 Retrieve results (Python)
```python
import json
from google import genai

client = genai.Client()

job_name = "YOUR_BATCH_JOB_NAME"
batch_job = client.batches.get(name=job_name)

if batch_job.state.name == 'JOB_STATE_SUCCEEDED':

    if batch_job.dest and batch_job.dest.file_name:
        result_file_name = batch_job.dest.file_name
        print(f"Results are in file: {result_file_name}")

        print("Downloading result file content...")
        file_content = client.files.download(file=result_file_name)
        print(file_content.decode('utf-8'))

    elif batch_job.dest and batch_job.dest.inlined_responses:
        print("Results are inline:")
        for i, inline_response in enumerate(batch_job.dest.inlined_responses):
            print(f"Response {i+1}:")
            if inline_response.response:
                try:
                    print(inline_response.response.text)
                except AttributeError:
                    print(inline_response.response)
            elif inline_response.error:
                print(f"Error: {inline_response.error}")
    else:
        print("No results found (neither file nor inline).")
else:
    print(f"Job did not succeed. Final state: {batch_job.state.name}")
    if batch_job.error:
        print(f"Error: {batch_job.error}")
```

#### G.13 Retrieve results (JavaScript)
```javascript
const jobName = "YOUR_BATCH_JOB_NAME";

try {
    const batchJob = await ai.batches.get({ name: jobName });

    if (batchJob.state === 'JOB_STATE_SUCCEEDED') {
        console.log('Found completed batch:', batchJob.displayName);
        console.log(batchJob);

        if (batchJob.dest?.fileName) {
            const resultFileName = batchJob.dest.fileName;
            console.log(`Results are in file: ${resultFileName}`);

            console.log("Downloading result file content...");
            const fileContentBuffer = await ai.files.download({ file: resultFileName });

            console.log(fileContentBuffer.toString('utf-8'));
        }

        else if (batchJob.dest?.inlinedResponses) {
            console.log("Results are inline:");
            for (let i = 0; i < batchJob.dest.inlinedResponses.length; i++) {
                const inlineResponse = batchJob.dest.inlinedResponses[i];
                console.log(`Response ${i + 1}:`);
                if (inlineResponse.response) {
                    if (inlineResponse.response.text !== undefined) {
                        console.log(inlineResponse.response.text);
                    } else {
                        console.log(inlineResponse.response);
                    }
                } else if (inlineResponse.error) {
                    console.error(`Error: ${inlineResponse.error}`);
                }
            }
        }

        else if (batchJob.dest?.inlinedEmbedContentResponses) {
            console.log("Embedding results found inline:");
            for (let i = 0; i < batchJob.dest.inlinedEmbedContentResponses.length; i++) {
                const inlineResponse = batchJob.dest.inlinedEmbedContentResponses[i];
                console.log(`Response ${i + 1}:`);
                if (inlineResponse.response) {
                    console.log(inlineResponse.response);
                } else if (inlineResponse.error) {
                    console.error(`Error: ${inlineResponse.error}`);
                }
            }
        } else {
            console.log("No results found (neither file nor inline).");
        }
    } else {
        console.log(`Job did not succeed. Final state: ${batchJob.state}`);
        if (batchJob.error) {
            console.error(`Error: ${typeof batchJob.error === 'string' ? batchJob.error : batchJob.error.message || JSON.stringify(batchJob.error)}`);
        }
    }
} catch (error) {
    console.error(`An error occurred while processing job ${jobName}:`, error);
}
```

#### G.14 Retrieve results (REST/curl)
```bash
BATCH_NAME="batches/123456" # Your batch job name

curl https://generativelanguage.googleapis.com/v1beta/$BATCH_NAME -H "x-goog-api-key: $GEMINI_API_KEY" -H "Content-Type:application/json" 2> /dev/null > batch_status.json

if jq -r '.done' batch_status.json | grep -q "false"; then
    echo "Batch has not finished processing"
fi

batch_state=$(jq -r '.metadata.state' batch_status.json)
if [[ $batch_state = "JOB_STATE_SUCCEEDED" ]]; then
    if [[ $(jq '.response | has("inlinedResponses")' batch_status.json) = "true" ]]; then
        jq -r '.response.inlinedResponses' batch_status.json
        exit
    fi
    responses_file_name=$(jq -r '.response.responsesFile' batch_status.json)
    curl https://generativelanguage.googleapis.com/download/v1beta/$responses_file_name:download?alt=media     -H "x-goog-api-key: $GEMINI_API_KEY" 2> /dev/null
elif [[ $batch_state = "JOB_STATE_FAILED" ]]; then
    jq '.error' batch_status.json
elif [[ $batch_state == "JOB_STATE_CANCELLED" ]]; then
    echo "Batch was cancelled by the user"
elif [[ $batch_state == "JOB_STATE_EXPIRED" ]]; then
    echo "Batch expired after 48 hours"
fi
```


### H. Video Segmentation Example (Python)
```python
from google import genai
from google.genai import types
from PIL import Image, ImageDraw
import io
import base64
import json
import numpy as np
import os

client = genai.Client()

def parse_json(json_output: str):
  # Parsing out the markdown fencing
  lines = json_output.splitlines()
  for i, line in enumerate(lines):
    if line == "```json":
      json_output = "
".join(lines[i+1:])  # Remove everything before "```json"
      output = json_output.split("```")[0]  # Remove everything after the closing "```"
      break  # Exit the loop once "```json" is found
  return json_output

def extract_segmentation_masks(image_path: str, output_dir: str = "segmentation_outputs"):
  # Load and resize image
  im = Image.open(image_path)
  im.thumbnail([1024, 1024], Image.Resampling.LANCZOS)

  prompt = """
  Give the segmentation masks for the wooden and glass items.
  Output a JSON list of segmentation masks where each entry contains the 2D
  bounding box in the key "box_2d", the segmentation mask in key "mask", and
  the text label in the key "label". Use descriptive labels.
  """

  config = types.GenerateContentConfig(
    thinking_config=types.ThinkingConfig(thinking_budget=0) # set thinking_budget to 0 for better results in object detection
  )

  response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents=[prompt, im], # Pillow images can be directly passed as inputs (which will be converted by the SDK)
    config=config
  )

  # Parse JSON response
  items = json.loads(parse_json(response.text))

  # Create output directory
  os.makedirs(output_dir, exist_ok=True)

  # Process each mask
  for i, item in enumerate(items):
      # Get bounding box coordinates
      box = item["box_2d"]
      y0 = int(box[0] / 1000 * im.size[1])
      x0 = int(box[1] / 1000 * im.size[0])
      y1 = int(box[2] / 1000 * im.size[1])
      x1 = int(box[3] / 1000 * im.size[0])

      # Skip invalid boxes
      if y0 >= y1 or x0 >= x1:
          continue

      # Process mask
      png_str = item["mask"]
      if not png_str.startswith("data:image/png;base64,"):
          continue

      png_str = png_str.removeprefix("data:image/png;base64,")
      mask_data = base64.b64decode(png_str)
      mask = Image.open(io.BytesIO(mask_data))

      mask = mask.resize((x1 - x0, y1 - y0), Image.Resampling.BILINEAR)

      mask_array = np.array(mask)

      overlay = Image.new('RGBA', im.size, (0, 0, 0, 0))
      overlay_draw = ImageDraw.Draw(overlay)

      color = (255, 255, 255, 200)
      for y in range(y0, y1):
          for x in range(x0, x1):
              if mask_array[y - y0, x - x0] > 128:  # Threshold for mask
                  overlay_draw.point((x, y), fill=color)

      mask_filename = f"{item['label']}_{i}_mask.png"
      overlay_filename = f"{item['label']}_{i}_overlay.png"

      mask.save(os.path.join(output_dir, mask_filename))

      composite = Image.alpha_composite(im.convert('RGBA'), overlay)
      composite.save(os.path.join(output_dir, overlay_filename))
      print(f"Saved mask and overlay for {item['label']} to {output_dir}")

if __name__ == "__main__":
  extract_segmentation_masks("path/to/image.png")
```

### I. Audio Input Examples

#### I.1 Upload audio file (Python)
```python
from google import genai

client = genai.Client()

myfile = client.files.upload(file="path/to/sample.mp3")

response = client.models.generate_content(
    model="gemini-2.5-flash", contents=["Describe this audio clip", myfile]
)

print(response.text)
```

#### I.2 Upload audio file (JavaScript)
```javascript
import {
  GoogleGenAI,
  createUserContent,
  createPartFromUri,
} from "@google/genai";

const ai = new GoogleGenAI({});

async function main() {
  const myfile = await ai.files.upload({
    file: "path/to/sample.mp3",
    config: { mimeType: "audio/mp3" },
  });

  const response = await ai.models.generateContent({
    model: "gemini-2.5-flash",
    contents: createUserContent([
      createPartFromUri(myfile.uri, myfile.mimeType),
      "Describe this audio clip",
    ]),
  });
  console.log(response.text);
}

await main();
```

#### I.3 Upload audio file (Go)
```go
file, err := client.UploadFileFromPath(ctx, "path/to/sample.mp3", nil)
if err != nil {
    log.Fatal(err)
}
defer client.DeleteFile(ctx, file.Name)

model := client.GenerativeModel("gemini-2.5-flash")
resp, err := model.GenerateContent(ctx,
    genai.FileData{URI: file.URI},
    genai.Text("Describe this audio clip"))
if err != nil {
    log.Fatal(err)
}

printResponse(resp)
```

#### I.4 Upload audio file (REST/curl)
```bash
AUDIO_PATH="path/to/sample.mp3"
MIME_TYPE=$(file -b --mime-type "${AUDIO_PATH}")
NUM_BYTES=$(wc -c < "${AUDIO_PATH}")
DISPLAY_NAME=AUDIO


curl "https://generativelanguage.googleapis.com/upload/v1beta/files" \
  -H "x-goog-api-key: $GEMINI_API_KEY" \
  -D upload-header.tmp \
  -H "X-Goog-Upload-Protocol: resumable" \
  -H "X-Goog-Upload-Command: start" \
  -H "X-Goog-Upload-Header-Content-Length: ${NUM_BYTES}" \
  -H "X-Goog-Upload-Header-Content-Type: ${MIME_TYPE}" \
  -H "Content-Type: application/json" \
  -d "{'file': {'display_name': '${DISPLAY_NAME}'}}" 2> /dev/null

upload_url=$(grep -i "x-goog-upload-url: " "${tmp_header_file}" | cut -d" " -f2 | tr -d "\r")
rm "${tmp_header_file}"

curl "${upload_url}" \
  -H "x-goog-api-key: $GEMINI_API_KEY" \
  -H "Content-Length: ${NUM_BYTES}" \
  -H "X-Goog-Upload-Offset: 0" \
  -H "X-Goog-Upload-Command: upload, finalize" \
  --data-binary "@${AUDIO_PATH}" 2> /dev/null > file_info.json

file_uri=$(jq -r ".file.uri" file_info.json)

echo "Generating content from audio..."
curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent" \
    -H "x-goog-api-key: $GEMINI_API_KEY" \
    -H 'Content-Type: application/json' \
    -X POST \
    -d '{
      "contents": [{
        "parts":[
          {"file_data":{"mime_type": "'"${MIME_TYPE}"'", "file_uri": "'"${file_uri}"'"}},
          {"text": "Describe this audio clip"}]
        }]
      }'
```

#### I.5 Inline audio data (Python)
```python
from google import genai
from google.genai import types

with open('path/to/small-sample.mp3', 'rb') as f:
    audio_bytes = f.read()

client = genai.Client()
response = client.models.generate_content(
  model='gemini-2.5-flash',
  contents=[
    'Please summarize the audio.',
    types.Part(
      inline_data=types.Blob(
        data=audio_bytes,
        mime_type='audio/mp3'),
    )
  ]
)

print(response.text)
```

#### I.6 Inline audio data (JavaScript)
```javascript
import { GoogleGenAI } from "@google/genai";
import * as fs from "node:fs";

const ai = new GoogleGenAI({});
const base64AudioFile = fs.readFileSync("path/to/small-sample.mp3", {
  encoding: "base64",
});

const contents = [
  { text: "Please summarize the audio." },
  {
    inlineData: {
      mimeType: "audio/mp3",
      data: base64AudioFile,
    },
  },
];

const response = await ai.models.generateContent({
  model: "gemini-2.5-flash",
  contents: contents,
});
console.log(response.text);
```

#### I.7 Inline audio data (Go)
```go
package main

import (
  "context"
  "fmt"
  "log"
  "os"
  "google.golang.org/genai"
)

func main() {
  ctx := context.Background()
  client, err := genai.NewClient(ctx, nil)
  if err != nil {
      log.Fatal(err)
  }

  audioBytes, _ := os.ReadFile("/path/to/small-sample.mp3")

  parts := []*genai.Part{
      genai.NewPartFromText("Describe this audio clip"),
    &genai.Part{
      InlineData: &genai.Blob{
        MIMEType: "audio/mp3",
        Data:     audioBytes,
      },
    },
  }
  contents := []*genai.Content{
      genai.NewContentFromParts(parts, genai.RoleUser),
  }

  result, _ := client.Models.GenerateContent(
      ctx,
      "gemini-2.5-flash",
      contents,
      nil,
  )

  fmt.Println(result.Text())
}
```

#### I.8 Inline audio data (REST/curl)
```bash
AUDIO_PATH=path/to/sample.mp3
MIME_TYPE=$(file -b --mime-type "${AUDIO_PATH}")
NUM_BYTES=$(wc -c < "${AUDIO_PATH}")
DISPLAY_NAME=AUDIO

curl "https://generativelanguage.googleapis.com/upload/v1beta/files" \
  -H "x-goog-api-key: $GEMINI_API_KEY" \
  -D upload-header.tmp \
  -H "X-Goog-Upload-Protocol: resumable" \
  -H "X-Goog-Upload-Command: start" \
  -H "X-Goog-Upload-Header-Content-Length: ${NUM_BYTES}" \
  -H "X-Goog-Upload-Header-Content-Type: ${MIME_TYPE}" \
  -H "Content-Type: application/json" \
  -d "{'file': {'display_name': '${DISPLAY_NAME}'}}" 2> /dev/null

upload_url=$(grep -i "x-goog-upload-url: " "upload-header.tmp" | cut -d" " -f2 | tr -d "\r")
rm "upload-header.tmp"

curl "${upload_url}" \
  -H "Content-Length: ${NUM_BYTES}" \
  -H "X-Goog-Upload-Offset: 0" \
  -H "X-Goog-Upload-Command: upload, finalize" \
  --data-binary "@${AUDIO_PATH}" 2> /dev/null > file_info.json

file_uri=$(jq -r ".file.uri" file_info.json)

echo "Generating content from audio..."
curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent" \
    -H "x-goog-api-key: $GEMINI_API_KEY" \
    -H 'Content-Type: application/json' \
    -X POST \
    -d '{
      "contents": [{
        "parts":[
          {"file_data":{"mime_type": "'"${MIME_TYPE}"'", "file_uri": "'"${file_uri}"'"}},
          {"text": "Describe this audio clip"}]
        }]
      }'
```

#### I.9 Get a transcript (Python)
```python
from google import genai

client = genai.Client()
myfile = client.files.upload(file='path/to/sample.mp3')
prompt = 'Generate a transcript of the speech.'

response = client.models.generate_content(
  model='gemini-2.5-flash',
  contents=[prompt, myfile]
)

print(response.text)
```

#### I.10 Get a transcript (JavaScript)
```javascript
import {
  GoogleGenAI,
  createUserContent,
  createPartFromUri,
} from "@google/genai";

const ai = new GoogleGenAI({});
const myfile = await ai.files.upload({
  file: "path/to/sample.mp3",
  config: { mimeType: "audio/mpeg" },
});

const result = await ai.models.generateContent({
  model: "gemini-2.5-flash",
  contents: createUserContent([
    createPartFromUri(myfile.uri, myfile.mimeType),
    "Generate a transcript of the speech.",
  ]),
});
console.log("result.text=", result.text);
```

#### I.11 Get a transcript (Go)
```go
package main

import (
  "context"
  "fmt"
  "log"
  "google.golang.org/genai"
)

func main() {
  ctx := context.Background()
  client, err := genai.NewClient(ctx, nil)
  if err != nil {
      log.Fatal(err)
  }

  localAudioPath := "/path/to/sample.mp3"
  uploadedFile, _ := client.Files.UploadFromPath(
      ctx,
      localAudioPath,
      nil,
  )

  parts := []*genai.Part{
      genai.NewPartFromText("Generate a transcript of the speech."),
      genai.NewPartFromURI(uploadedFile.URI, uploadedFile.MIMEType),
  }
  contents := []*genai.Content{
      genai.NewContentFromParts(parts, genai.RoleUser),
  }

  result, _ := client.Models.GenerateContent(
      ctx,
      "gemini-2.5-flash",
      contents,
      nil,
  )

  fmt.Println(result.Text())
}
```

#### I.12 Transcript with timestamps (Python prompt)
```python
prompt = "Provide a transcript of the speech from 02:30 to 03:29."
```

#### I.13 Transcript with timestamps (JavaScript prompt)
```javascript
const prompt = "Provide a transcript of the speech from 02:30 to 03:29.";
```

#### I.14 Transcript with timestamps (Go prompt)
```go
prompt := []*genai.Part{
    genai.NewPartFromURI(uploadedFile.URI, uploadedFile.MIMEType),
    genai.NewPartFromText("Provide a transcript of the speech between the timestamps 02:30 and 03:29."),
}
```

#### I.15 Transcript with timestamps (REST prompt)
```bash
prompt="Provide a transcript of the speech from 02:30 to 03:29."
```

#### I.16 Counting tokens for audio (Python)
```python
from google import genai

client = genai.Client()
response = client.models.count_tokens(
  model='gemini-2.5-flash',
  contents=[myfile]
)

print(response)
```
### J. Document Understanding Examples

#### J.1 Inline PDF (Python)
```python
from google import genai
from google.genai import types
import httpx

client = genai.Client()

doc_url = "https://discovery.ucl.ac.uk/id/eprint/10089234/1/343019_3_art_0_py4t4l_convrt.pdf"

doc_data = httpx.get(doc_url).content

prompt = "Summarize this document"
response = client.models.generate_content(
  model="gemini-2.5-flash",
  contents=[
      types.Part.from_bytes(
        data=doc_data,
        mime_type='application/pdf',
      ),
      prompt])
print(response.text)
```

#### J.2 Inline PDF (JavaScript)
```javascript
import { GoogleGenAI } from "@google/genai";

const ai = new GoogleGenAI({ apiKey: "GEMINI_API_KEY" });

async function main() {
    const pdfResp = await fetch('https://discovery.ucl.ac.uk/id/eprint/10089234/1/343019_3_art_0_py4t4l_convrt.pdf')
        .then((response) => response.arrayBuffer());

    const contents = [
        { text: "Summarize this document" },
        {
            inlineData: {
                mimeType: 'application/pdf',
                data: Buffer.from(pdfResp).toString("base64")
            }
        }
    ];

    const response = await ai.models.generateContent({
        model: "gemini-2.5-flash",
        contents: contents
    });
    console.log(response.text);
}

main();
```

#### J.3 Inline PDF (Go)
```go
package main

import (
    "context"
    "fmt"
    "io"
    "net/http"
    "os"
    "google.golang.org/genai"
)

func main() {

    ctx := context.Background()
    client, _ := genai.NewClient(ctx, &genai.ClientConfig{
        APIKey:  os.Getenv("GEMINI_API_KEY"),
        Backend: genai.BackendGeminiAPI,
    })

    pdfResp, _ := http.Get("https://discovery.ucl.ac.uk/id/eprint/10089234/1/343019_3_art_0_py4t4l_convrt.pdf")
    var pdfBytes []byte
    if pdfResp != nil && pdfResp.Body != nil {
        pdfBytes, _ = io.ReadAll(pdfResp.Body)
        pdfResp.Body.Close()
    }

    parts := []*genai.Part{
        &genai.Part{
            InlineData: &genai.Blob{
                MIMEType: "application/pdf",
                Data:     pdfBytes,
            },
        },
        genai.NewPartFromText("Summarize this document"),
    }

    contents := []*genai.Content{
        genai.NewContentFromParts(parts, genai.RoleUser),
    }

    result, _ := client.Models.GenerateContent(
        ctx,
        "gemini-2.5-flash",
        contents,
        nil,
    )

    fmt.Println(result.Text())
}
```

#### J.4 Inline PDF (REST/curl)
```bash
DOC_URL="https://discovery.ucl.ac.uk/id/eprint/10089234/1/343019_3_art_0_py4t4l_convrt.pdf"
PROMPT="Summarize this document"
DISPLAY_NAME="base64_pdf"

wget -O "${DISPLAY_NAME}.pdf" "${DOC_URL}"

if [[ "$(base64 --version 2>&1)" = *"FreeBSD"* ]]; then
  B64FLAGS="--input"
else
  B64FLAGS="-w0"
fi

ENCODED_PDF=$(base64 $B64FLAGS "${DISPLAY_NAME}.pdf")

curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=$GOOGLE_API_KEY" \
    -H 'Content-Type: application/json' \
    -X POST \
    -d '{
      "contents": [{
        "parts":[
            {
              "inline_data": {
                "mime_type":"application/pdf",
                "data": "'"${ENCODED_PDF}"'"
              }
            },
            {"text": "'"${PROMPT}"'"}
        ]
      }]
    }' 2> /dev/null > response.json

cat response.json
jq ".candidates[].content.parts[].text" response.json

rm "${DISPLAY_NAME}.pdf"
```

#### J.5 Inline PDF from local file (Python)
```python
from google import genai
from google.genai import types
import pathlib

client = genai.Client()

filepath = pathlib.Path('file.pdf')

prompt = "Summarize this document"
response = client.models.generate_content(
  model="gemini-2.5-flash",
  contents=[
      types.Part.from_bytes(
        data=filepath.read_bytes(),
        mime_type='application/pdf',
      ),
      prompt])
print(response.text)
```

### K. Files API Utilities

#### K.1 Get metadata (Python)
```python
myfile = client.files.upload(file='path/to/sample.mp3')
file_name = myfile.name
myfile = client.files.get(name=file_name)
print(myfile)
```

#### K.2 Get metadata (JavaScript)
```javascript
const myfile = await ai.files.upload({
  file: "path/to/sample.mp3",
  config: { mimeType: "audio/mpeg" },
});

const fileName = myfile.name;
const fetchedFile = await ai.files.get({ name: fileName });
console.log(fetchedFile);
```

#### K.3 Get metadata (Go)
```go
file, err := client.UploadFileFromPath(ctx, "path/to/sample.mp3", nil)
if err != nil {
    log.Fatal(err)
}

gotFile, err := client.GetFile(ctx, file.Name)
if err != nil {
    log.Fatal(err)
}
fmt.Println("Got file:", gotFile.Name)
```

#### K.4 Get metadata (REST/curl)
```bash
# file_info.json was created in the upload example
name=$(jq ".file.name" file_info.json)
# Get the file of interest to check state
curl https://generativelanguage.googleapis.com/v1beta/files/$name -H "x-goog-api-key: $GEMINI_API_KEY" > file_info.json
# Print some information about the file you got
name=$(jq ".file.name" file_info.json)
echo name=$name
file_uri=$(jq ".file.uri" file_info.json)
echo file_uri=$file_uri
```

#### K.5 List files (Python)
```python
print('My files:')
for f in client.files.list():
    print(' ', f.name)
```

#### K.6 List files (JavaScript)
```javascript
const listResponse = await ai.files.list({ config: { pageSize: 10 } });
for await (const file of listResponse) {
  console.log(file.name);
}
```

#### K.7 List files (Go)
```go
iter := client.ListFiles(ctx)
for {
    ifile, err := iter.Next()
    if err == iterator.Done {
        break
    }
    if err != nil {
        log.Fatal(err)
    }
    fmt.Println(ifile.Name)
}
```

#### K.8 List files (REST/curl)
```bash
echo "My files: "

curl "https://generativelanguage.googleapis.com/v1beta/files"   -H "x-goog-api-key: $GEMINI_API_KEY"
```

#### K.9 Delete file (Python)
```python
myfile = client.files.upload(file='path/to/sample.mp3')
client.files.delete(name=myfile.name)
```

#### K.10 Delete file (JavaScript)
```javascript
const myfile = await ai.files.upload({
  file: "path/to/sample.mp3",
  config: { mimeType: "audio/mpeg" },
});

const fileName = myfile.name;
await ai.files.delete({ name: fileName });
```

#### K.11 Delete file (Go)
```go
file, err := client.UploadFileFromPath(ctx, "path/to/sample.mp3", nil)
if err != nil {
    log.Fatal(err)
}
client.DeleteFile(ctx, file.Name)
```

#### K.12 Delete file (REST/curl)
```bash
curl --request "DELETE" https://generativelanguage.googleapis.com/v1beta/files/$name   -H "x-goog-api-key: $GEMINI_API_KEY"
```

### L. Context Caching Example (Python)
```python
import os
import pathlib
import requests
import time

from google import genai
from google.genai import types

client = genai.Client()

url = 'https://storage.googleapis.com/generativeai-downloads/data/SherlockJr._10min.mp4'
path_to_video_file = pathlib.Path('SherlockJr._10min.mp4')
if not path_to_video_file.exists():
  with path_to_video_file.open('wb') as wf:
    response = requests.get(url, stream=True)
    for chunk in response.iter_content(chunk_size=32768):
      wf.write(chunk)

video_file = client.files.upload(file=path_to_video_file)
while video_file.state.name == 'PROCESSING':
  print('Waiting for video to be processed.')
  time.sleep(2)
  video_file = client.files.get(name=video_file.name)

print(f'Video processing complete: {video_file.uri}')

model='models/gemini-2.0-flash-001'

cache = client.caches.create(
    model=model,
    config=types.CreateCachedContentConfig(
      display_name='sherlock jr movie',
      system_instruction=(
          'You are an expert video analyzer, and your job is to answer '
          'the user's query based on the video file you have access to.'
      ),
      contents=[video_file],
      ttl="300s",
  )
)

response = client.models.generate_content(
  model = model,
  contents= (
    'Introduce different characters in the movie by describing '
    'their personality, looks, and names. Also list the timestamps '
    'they were introduced for the first time.'),
  config=types.GenerateContentConfig(cached_content=cache.name)
)

print(response.usage_metadata)
print(response.text)
```
### M. Thinking Examples

#### M.1 Thought summaries (Python)
```python
from google import genai
from google.genai import types

client = genai.Client()
prompt = "What is the sum of the first 50 prime numbers?"
response = client.models.generate_content(
  model="gemini-2.5-pro",
  contents=prompt,
  config=types.GenerateContentConfig(
    thinking_config=types.ThinkingConfig(
      include_thoughts=True
    )
  )
)

for part in response.candidates[0].content.parts:
  if not part.text:
    continue
  if part.thought:
    print("Thought summary:")
    print(part.text)
    print()
  else:
    print("Answer:")
    print(part.text)
    print()
```

#### M.2 Thought summaries (JavaScript)
```javascript
import { GoogleGenAI } from "@google/genai";

const ai = new GoogleGenAI({});

async function main() {
  const response = await ai.models.generateContent({
    model: "gemini-2.5-pro",
    contents: "What is the sum of the first 50 prime numbers?",
    config: {
      thinkingConfig: {
        includeThoughts: true,
      },
    },
  });

  for (const part of response.candidates[0].content.parts) {
    if (!part.text) {
      continue;
    }
    else if (part.thought) {
      console.log("Thoughts summary:");
      console.log(part.text);
    }
    else {
      console.log("Answer:");
      console.log(part.text);
    }
  }
}

main();
```

#### M.3 Thought summaries (Go)
```go
package main

import (
  "context"
  "fmt"
  "log"
  "google.golang.org/genai"
)

func main() {
  ctx := context.Background()
  client, err := genai.NewClient(ctx, nil)
  if err != nil {
      log.Fatal(err)
  }

  contents := genai.Text("What is the sum of the first 50 prime numbers?")
  model := "gemini-2.5-pro"
  resp, _ := client.Models.GenerateContent(ctx, model, contents, &genai.GenerateContentConfig{
    ThinkingConfig: &genai.ThinkingConfig{
      IncludeThoughts: true,
    },
  })

  for _, part := range resp.Candidates[0].Content.Parts {
    if part.Text != "" {
      if part.Thought {
        fmt.Println("Thoughts Summary:")
        fmt.Println(part.Text)
      } else {
        fmt.Println("Answer:")
        fmt.Println(part.Text)
      }
    }
  }
}
```

#### M.4 Streaming thought summaries (Python)
```python
from google import genai
from google.genai import types

client = genai.Client()

prompt = """
Alice, Bob, and Carol each live in a different house on the same street: red, green, and blue.
The person who lives in the red house owns a cat.
Bob does not live in the green house.
Carol owns a dog.
The green house is to the left of the red house.
Alice does not own a cat.
Who lives in each house, and what pet do they own?
"""

thoughts = ""
answer = ""

for chunk in client.models.generate_content_stream(
    model="gemini-2.5-pro",
    contents=prompt,
    config=types.GenerateContentConfig(
      thinking_config=types.ThinkingConfig(
        include_thoughts=True
      )
    )
):
  for part in chunk.candidates[0].content.parts:
      if not part.text:
        continue
      elif part.thought:
        if not thoughts:
          print("Thoughts summary:")
        print(part.text)
        thoughts += part.text
      else:
        if not answer:
          print("Answer:")
        print(part.text)
        answer += part.text
```

#### M.5 Streaming thought summaries (JavaScript)
```javascript
import { GoogleGenAI } from "@google/genai";

const ai = new GoogleGenAI({});

const prompt = `Alice, Bob, and Carol each live in a different house on the same
street: red, green, and blue. The person who lives in the red house owns a cat.
Bob does not live in the green house. Carol owns a dog. The green house is to
the left of the red house. Alice does not own a cat. Who lives in each house,
and what pet do they own?`;

let thoughts = "";
let answer = "";

async function main() {
  const response = await ai.models.generateContentStream({
    model: "gemini-2.5-pro",
    contents: prompt,
    config: {
      thinkingConfig: {
        includeThoughts: true,
      },
    },
  });

  for await (const chunk of response) {
    for (const part of chunk.candidates[0].content.parts) {
      if (!part.text) {
        continue;
      } else if (part.thought) {
        if (!thoughts) {
          console.log("Thoughts summary:");
        }
        console.log(part.text);
        thoughts = thoughts + part.text;
      } else {
        if (!answer) {
          console.log("Answer:");
        }
        console.log(part.text);
        answer = answer + part.text;
      }
    }
  }
}

await main();
```

#### M.6 Streaming thought summaries (Go)
```go
package main

import (
  "context"
  "fmt"
  "log"
  "google.golang.org/genai"
)

const prompt = `
Alice, Bob, and Carol each live in a different house on the same street: red, green, and blue.
The person who lives in the red house owns a cat.
Bob does not live in the green house.
Carol owns a dog.
The green house is to the left of the red house.
Alice does not own a cat.
Who lives in each house, and what pet do they own?
`

func main() {
  ctx := context.Background()
  client, err := genai.NewClient(ctx, nil)
  if err != nil {
      log.Fatal(err)
  }

  contents := genai.Text(prompt)
  model := "gemini-2.5-pro"

  resp := client.Models.GenerateContentStream(ctx, model, contents, &genai.GenerateContentConfig{
    ThinkingConfig: &genai.ThinkingConfig{
      IncludeThoughts: true,
    },
  })

  for chunk := range resp {
    for _, part := range chunk.Candidates[0].Content.Parts {
      if len(part.Text) == 0 {
        continue
      }

      if part.Thought {
        fmt.Printf("Thought: %s\n", part.Text)
      } else {
        fmt.Printf("Answer: %s\n", part.Text)
      }
    }
  }
}
```
### N. Function Calling Examples

#### N.1 Define function (Python)
```python
from google import genai
from google.genai import types

# Define the function declaration for the model
schedule_meeting_function = {
    "name": "schedule_meeting",
    "description": "Schedules a meeting with specified attendees at a given time and date.",
    "parameters": {
        "type": "object",
        "properties": {
            "attendees": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of people attending the meeting.",
            },
            "date": {
                "type": "string",
                "description": "Date of the meeting (e.g., '2024-07-29')",
            },
            "time": {
                "type": "string",
                "description": "Time of the meeting (e.g., '15:00')",
            },
            "topic": {
                "type": "string",
                "description": "The subject or topic of the meeting.",
            },
        },
        "required": ["attendees", "date", "time", "topic"],
    },
}

client = genai.Client()
tools = types.Tool(function_declarations=[schedule_meeting_function])
config = types.GenerateContentConfig(tools=[tools])

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="Schedule a meeting with Bob and Alice for 03/14/2025 at 10:00 AM about the Q3 planning.",
    config=config,
)

if response.candidates[0].content.parts[0].function_call:
    function_call = response.candidates[0].content.parts[0].function_call
    print(f"Function to call: {function_call.name}")
    print(f"Arguments: {function_call.args}")
else:
    print("No function call found in the response.")
    print(response.text)
```

#### N.2 Define function (JavaScript)
```javascript
import { GoogleGenAI, Type } from '@google/genai';

const scheduleMeetingFunctionDeclaration = {
  name: 'schedule_meeting',
  description: 'Schedules a meeting with specified attendees at a given time and date.',
  parameters: {
    type: Type.OBJECT,
    properties: {
      attendees: {
        type: Type.ARRAY,
        items: { type: Type.STRING },
        description: 'List of people attending the meeting.',
      },
      date: {
        type: Type.STRING,
        description: 'Date of the meeting (e.g., "2024-07-29")',
      },
      time: {
        type: Type.STRING,
        description: 'Time of the meeting (e.g., "15:00")',
      },
      topic: {
        type: Type.STRING,
        description: 'The subject or topic of the meeting.',
      },
    },
    required: ['attendees', 'date', 'time', 'topic'],
  },
};

const ai = new GoogleGenAI({});

const response = await ai.models.generateContent({
  model: 'gemini-2.5-flash',
  contents: 'Schedule a meeting with Bob and Alice for 03/27/2025 at 10:00 AM about the Q3 planning.',
  config: {
    tools: [{
      functionDeclarations: [scheduleMeetingFunctionDeclaration]
    }],
  },
});

if (response.functionCalls && response.functionCalls.length > 0) {
  const functionCall = response.functionCalls[0];
  console.log(`Function to call: ${functionCall.name}`);
  console.log(`Arguments: ${JSON.stringify(functionCall.args)}`);
} else {
  console.log("No function call found in the response.");
  console.log(response.text);
}
```

#### N.3 Execute suggested function (Python)
```python
# Extract tool call details, it may not be in the first part.
tool_call = response.candidates[0].content.parts[0].function_call

if tool_call.name == "schedule_meeting":
    result = schedule_meeting(**tool_call.args)
    print(f"Function execution result: {result}")
```

#### N.4 Function response loop (Python)
```python
function_response_part = types.Part.from_function_response(
    name=tool_call.name,
    response={"result": result},
)

contents.append(response.candidates[0].content)
contents.append(types.Content(role="user", parts=[function_response_part]))

final_response = client.models.generate_content(
    model="gemini-2.5-flash",
    config=config,
    contents=contents,
)

print(final_response.text)
```

#### N.5 Parallel function calling (Python excerpt)
```python
power_disco_ball = {
    "name": "power_disco_ball",
    "description": "Powers the spinning disco ball.",
    "parameters": {
        "type": "object",
        "properties": {
            "power": {
                "type": "boolean",
                "description": "Whether to turn the disco ball on or off.",
            }
        },
        "required": ["power"],
    },
}

start_music = {
    "name": "start_music",
    "description": "Play some music matching the specified parameters.",
    "parameters": {
        "type": "object",
        "properties": {
            "energetic": {
                "type": "boolean",
                "description": "Whether the music is energetic or not.",
            },
            "loud": {
                "type": "boolean",
                "description": "Whether the music is loud or not.",
            },
        },
        "required": ["energetic", "loud"],
    },
}

house_tools = [
    types.Tool(function_declarations=[power_disco_ball, start_music])
]
config = types.GenerateContentConfig(
    tools=house_tools,
    automatic_function_calling=types.AutomaticFunctionCallingConfig(
        disable=True
    ),
    tool_config=types.ToolConfig(
        function_calling_config=types.FunctionCallingConfig(mode='ANY')
    ),
)

chat = client.chats.create(model="gemini-2.5-flash", config=config)
response = chat.send_message("Turn this place into a party!")
```

#### N.6 Automatic function calling (Python)
```python
from google import genai
from google.genai import types

# Actual function implementations
def power_disco_ball_impl(power: bool) -> dict:
    return {"status": f"Disco ball powered {'on' if power else 'off'}"}

def start_music_impl(energetic: bool, loud: bool) -> dict:
    music_type = "energetic" if energetic else "chill"
    volume = "loud" if loud else "quiet"
    return {"music_type": music_type, "volume": volume}

def dim_lights_impl(brightness: float) -> dict:
    return {"brightness": brightness}

client = genai.Client()
config = types.GenerateContentConfig(
    tools=[power_disco_ball_impl, start_music_impl, dim_lights_impl]
)

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="Do everything you need to this place into party!",
    config=config,
)

print("\nExample 2: Automatic function calling")
print(response.text)
```

#### N.7 Function calling with thinking (Python)
```python
from google import genai
from google.genai import types

client = genai.Client()
response = client.models.generate_content(
    model="gemini-2.5-pro",
    contents="Provide a list of 3 famous physicists and their key contributions",
    config=types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_budget=1024)
    ),
)

print(response.text)
```

#### J.6 Upload large PDF via Files API (Python)
```python
from google import genai
from google.genai import types
import io
import httpx

client = genai.Client()

long_context_pdf_path = "https://www.nasa.gov/wp-content/uploads/static/history/alsj/a17/A17_FlightPlan.pdf"

doc_io = io.BytesIO(httpx.get(long_context_pdf_path).content)

sample_doc = client.files.upload(
  file=doc_io,
  config=dict(
    mime_type='application/pdf')
)

prompt = "Summarize this document"

response = client.models.generate_content(
  model="gemini-2.5-flash",
  contents=[sample_doc, prompt])
print(response.text)
```

#### J.7 Upload large PDF via Files API (JavaScript)
```javascript
import { createPartFromUri, GoogleGenAI } from "@google/genai";

const ai = new GoogleGenAI({ apiKey: "GEMINI_API_KEY" });

async function main() {

    const pdfBuffer = await fetch("https://www.nasa.gov/wp-content/uploads/static/history/alsj/a17/A17_FlightPlan.pdf")
        .then((response) => response.arrayBuffer());

    const fileBlob = new Blob([pdfBuffer], { type: 'application/pdf' });

    const file = await ai.files.upload({
        file: fileBlob,
        config: {
            displayName: 'A17_FlightPlan.pdf',
        },
    });

    let getFile = await ai.files.get({ name: file.name });
    while (getFile.state === 'PROCESSING') {
        getFile = await ai.files.get({ name: file.name });
        console.log(`current file status: ${getFile.state}`);
        console.log('File is still processing, retrying in 5 seconds');

        await new Promise((resolve) => {
            setTimeout(resolve, 5000);
        });
    }
    if (file.state === 'FAILED') {
        throw new Error('File processing failed.');
    }

    const content = [
        'Summarize this document',
    ];

    if (file.uri && file.mimeType) {
        const fileContent = createPartFromUri(file.uri, file.mimeType);
        content.push(fileContent);
    }

    const response = await ai.models.generateContent({
        model: 'gemini-2.5-flash',
        contents: content,
    });

    console.log(response.text);

}

main();
```
#### C.8 Enum using Literal (Python)
```python
from typing import Literal
from google import genai

client = genai.Client()
response = client.models.generate_content(
    model='gemini-2.5-flash',
    contents='What type of instrument is an oboe?',
    config={
        'response_mime_type': 'text/x.enum',
        'response_schema': Literal["Percussion", "String", "Woodwind", "Brass", "Keyboard"],
    },
)

print(response.text)
```

#### C.9 Enum schema as JSON (Python)
```python
from google import genai

client = genai.Client()
response = client.models.generate_content(
    model='gemini-2.5-flash',
    contents='What type of instrument is an oboe?',
    config={
        'response_mime_type': 'text/x.enum',
        'response_schema': {
            "type": "STRING",
            "enum": ["Percussion", "String", "Woodwind", "Brass", "Keyboard"],
        },
    },
)

print(response.text)
```

#### F.16 Batch embeddings (Python)
```python
from google import genai

client = genai.Client()

file_job = client.batches.create_embeddings(
    model="gemini-embedding-001",
    src={'file_name': uploaded_batch_requests.name},
    config={'display_name': "Input embeddings batch"},
)

batch_job = client.batches.create_embeddings(
    model="gemini-embedding-001",
    src={'inlined_requests': inlined_requests},
    config={'display_name': "Inlined embeddings batch"},
)
```

#### F.17 Batch embeddings (JavaScript)
```javascript
let fileJob;
fileJob = await client.batches.createEmbeddings({
    model: 'gemini-embedding-001',
    src: {fileName: uploadedBatchRequests.name},
    config: {displayName: 'Input embeddings batch'},
});
console.log(`Created batch job: ${fileJob.name}`);

let batchJob;
batchJob = await client.batches.createEmbeddings({
    model: 'gemini-embedding-001',
    src: {inlinedRequests: inlinedRequests},
    config: {displayName: 'Inlined embeddings batch'},
});
console.log(`Created batch job: ${batchJob.name}`);
```
