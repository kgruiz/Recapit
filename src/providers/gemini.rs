use std::collections::HashMap;
use std::fs;
use std::sync::Mutex;

use anyhow::{anyhow, Context, Result};
use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine;
use reqwest::blocking::Client;
use reqwest::header::{HeaderMap, HeaderValue, CONTENT_LENGTH, CONTENT_TYPE};
use serde_json::{json, Value};
use time::OffsetDateTime;

use crate::constants::{model_capabilities, DEFAULT_MODEL};
use crate::core::{Asset, Provider, SourceKind};
use crate::telemetry::{RequestEvent, RunMonitor};

const INLINE_THRESHOLD_BYTES: usize = 20 * 1024 * 1024;

pub struct GeminiProvider {
    api_key: String,
    model: String,
    http: Client,
    monitor: RunMonitor,
    upload_cache: Mutex<HashMap<String, CachedUpload>>,
}

#[derive(Clone)]
struct CachedUpload {
    uri: String,
    mime_type: String,
}

impl GeminiProvider {
    pub fn new(api_key: String, model: String, monitor: RunMonitor) -> Self {
        let http = Client::builder()
            .timeout(std::time::Duration::from_secs(600))
            .build()
            .expect("failed to build reqwest client");
        Self {
            api_key,
            model,
            http,
            monitor,
            upload_cache: Mutex::new(HashMap::new()),
        }
    }

    fn part_for_asset(&self, asset: &Asset) -> Result<(Value, HashMap<String, Value>)> {
        let mut metadata = HashMap::new();
        if let Some(obj) = asset.meta.as_object() {
            for (key, value) in obj {
                metadata.insert(key.clone(), value.clone());
            }
        }

        let mime = asset
            .mime
            .clone()
            .or_else(|| {
                mime_guess::from_path(&asset.path)
                    .first_raw()
                    .map(|s| s.to_string())
            })
            .unwrap_or_else(|| "application/octet-stream".to_string());

        if asset.source_kind == SourceKind::Youtube
            && asset.meta.get("pass_through").and_then(|v| v.as_bool()) == Some(true)
        {
            if let Some(url) = asset
                .meta
                .get("source_url")
                .and_then(|value| value.as_str())
            {
                metadata.insert("file_uri".into(), Value::String(url.to_string()));
                let part = json!({
                    "file_data": {
                        "file_uri": url,
                        "mime_type": mime,
                    }
                });
                return Ok((part, metadata));
            }
        }

        if let Some(inline_bytes) = asset
            .meta
            .get("inline_bytes")
            .and_then(|value| value.as_str())
        {
            let part = json!({
                "inline_data": {
                    "data": inline_bytes,
                    "mime_type": mime,
                }
            });
            return Ok((part, metadata));
        }

        let bytes = fs::read(&asset.path)
            .with_context(|| format!("reading asset {}", asset.path.display()))?;
        if bytes.len() <= INLINE_THRESHOLD_BYTES {
            let encoded = BASE64.encode(&bytes);
            let part = json!({
                "inline_data": {
                    "data": encoded,
                    "mime_type": mime,
                }
            });
            return Ok((part, metadata));
        }

        if let Some(key) = asset.meta.get("upload_cache_key").and_then(|v| v.as_str()) {
            if let Some(cached) = self.upload_cache.lock().unwrap().get(key).cloned() {
                let part = json!({
                    "file_data": {
                        "file_uri": cached.uri,
                        "mime_type": cached.mime_type,
                    }
                });
                metadata.insert("file_uri".into(), Value::String(cached.uri));
                return Ok((part, metadata));
            }
        }

        let upload = self.upload_file(asset, &bytes, &mime)?;
        if let Some(cache_key) = asset.meta.get("upload_cache_key").and_then(|v| v.as_str()) {
            self.upload_cache.lock().unwrap().insert(
                cache_key.to_string(),
                CachedUpload {
                    uri: upload.uri.clone(),
                    mime_type: upload.mime_type.clone(),
                },
            );
        }
        metadata.insert("file_uri".into(), Value::String(upload.uri.clone()));
        let part = json!({
            "file_data": {
                "file_uri": upload.uri,
                "mime_type": upload.mime_type,
            }
        });
        Ok((part, metadata))
    }

    fn upload_file(&self, asset: &Asset, bytes: &[u8], mime: &str) -> Result<CachedUpload> {
        let start_url = format!(
            "https://generativelanguage.googleapis.com/v1beta/files:upload?key={}",
            self.api_key
        );

        let display_name = asset
            .path
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or_else(|| "upload");

        let mut headers = HeaderMap::new();
        headers.insert(
            "X-Goog-Upload-Protocol",
            HeaderValue::from_static("resumable"),
        );
        headers.insert("X-Goog-Upload-Command", HeaderValue::from_static("start"));
        let start_length = bytes.len().to_string();
        headers.insert(
            "X-Goog-Upload-Header-Content-Length",
            HeaderValue::from_str(&start_length)?,
        );
        headers.insert(
            "X-Goog-Upload-Header-Content-Type",
            HeaderValue::from_str(mime)?,
        );

        let start_resp = self
            .http
            .post(&start_url)
            .headers(headers)
            .json(&json!({"file": {"display_name": display_name}}))
            .send()
            .context("starting resumable upload")?;
        if !start_resp.status().is_success() {
            return Err(anyhow!(
                "files:upload start failed with status {}",
                start_resp.status()
            ));
        }

        let upload_url = start_resp
            .headers()
            .get("X-Goog-Upload-URL")
            .or_else(|| start_resp.headers().get("x-goog-upload-url"))
            .ok_or_else(|| anyhow!("missing X-Goog-Upload-URL header"))?
            .to_str()
            .context("parsing upload URL header")?
            .to_string();

        let mut upload_headers = HeaderMap::new();
        upload_headers.insert(
            "X-Goog-Upload-Command",
            HeaderValue::from_static("upload, finalize"),
        );
        upload_headers.insert("X-Goog-Upload-Offset", HeaderValue::from_static("0"));
        upload_headers.insert(CONTENT_TYPE, HeaderValue::from_str(mime)?);
        let upload_length = bytes.len().to_string();
        upload_headers.insert(CONTENT_LENGTH, HeaderValue::from_str(&upload_length)?);

        let finalize_resp = self
            .http
            .post(&upload_url)
            .headers(upload_headers)
            .body(bytes.to_owned())
            .send()
            .context("uploading file data")?;
        if !finalize_resp.status().is_success() {
            return Err(anyhow!(
                "files:upload finalize failed with status {}",
                finalize_resp.status()
            ));
        }

        let value: Value = finalize_resp.json().context("decoding upload response")?;
        let uri = value
            .get("file")
            .and_then(|f| f.get("uri"))
            .and_then(|v| v.as_str())
            .ok_or_else(|| anyhow!("upload response missing file.uri"))?
            .to_string();
        Ok(CachedUpload {
            uri,
            mime_type: mime.to_string(),
        })
    }
}

impl Provider for GeminiProvider {
    fn supports(&self, capability: &str) -> bool {
        let table = model_capabilities();
        table
            .get(self.model.as_str())
            .or_else(|| table.get(DEFAULT_MODEL))
            .map(|caps| caps.iter().any(|cap| *cap == capability))
            .unwrap_or(true)
    }

    fn transcribe(
        &self,
        instruction: &str,
        assets: &[Asset],
        modality: &str,
        meta: &serde_json::Value,
    ) -> Result<String> {
        let mut parts = Vec::new();
        let mut event_metadata = HashMap::new();
        if let Some(obj) = meta.as_object() {
            for (key, value) in obj {
                event_metadata.insert(key.clone(), value.clone());
            }
        }
        parts.push(json!({"text": instruction}));
        for asset in assets {
            let (part, metadata) = self.part_for_asset(asset)?;
            for (key, value) in metadata {
                event_metadata.entry(key).or_insert(value);
            }
            parts.push(part);
        }

        let request = json!({
            "contents": [
                {
                    "role": "user",
                    "parts": parts,
                }
            ]
        });

        let url = format!(
            "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent",
            self.model
        );

        let started = OffsetDateTime::now_utc();
        let response = self
            .http
            .post(&url)
            .query(&[("key", self.api_key.as_str())])
            .json(&request)
            .send()
            .context("calling generateContent")?;

        if !response.status().is_success() {
            return Err(anyhow!(
                "generateContent failed with status {}",
                response.status()
            ));
        }

        let finished = OffsetDateTime::now_utc();
        let payload: Value = response
            .json()
            .context("parsing generateContent response")?;
        let text = payload
            .get("candidates")
            .and_then(|candidates| candidates.as_array())
            .and_then(|array| array.first())
            .and_then(|cand| cand.get("content"))
            .and_then(|content| content.get("parts"))
            .and_then(|parts| parts.as_array())
            .map(|parts| {
                parts
                    .iter()
                    .filter_map(|part| part.get("text").and_then(|t| t.as_str()))
                    .collect::<Vec<_>>()
                    .join("\n")
            })
            .unwrap_or_default();

        let usage = payload.get("usageMetadata");
        let input_tokens = usage
            .and_then(|u| u.get("promptTokenCount"))
            .and_then(|v| v.as_u64())
            .map(|v| v as u32);
        let output_tokens = usage
            .and_then(|u| u.get("candidatesTokenCount"))
            .and_then(|v| v.as_u64())
            .map(|v| v as u32);
        let total_tokens = usage
            .and_then(|u| u.get("totalTokenCount"))
            .and_then(|v| v.as_u64())
            .map(|v| v as u32);

        self.monitor.record(RequestEvent {
            model: self.model.clone(),
            modality: modality.to_string(),
            started_at: started,
            finished_at: finished,
            input_tokens,
            output_tokens,
            total_tokens,
            metadata: event_metadata,
        });

        Ok(text)
    }
}
