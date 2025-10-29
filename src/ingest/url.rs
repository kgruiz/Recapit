use anyhow::Result;
use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine;
use reqwest::blocking::{Client, Response};
use reqwest::header::{CONTENT_LENGTH, CONTENT_TYPE};
use sha2::{Digest, Sha256};
use std::fs::File;
use std::io::{copy, Read, Write};
use std::path::{Path, PathBuf};
use std::str::FromStr;
use url::Url;

use crate::core::{Asset, Job, SourceKind};
use crate::utils::ensure_dir;

const INLINE_THRESHOLD: usize = 20 * 1024 * 1024;

pub struct UrlIngestor {
    client: Client,
    cache_dir: PathBuf,
}

impl UrlIngestor {
    pub fn new(cache_dir: Option<PathBuf>) -> Result<Self> {
        let cache = cache_dir.unwrap_or_else(|| std::env::temp_dir().join("recapit-url-cache"));
        ensure_dir(&cache)?;
        Ok(Self {
            client: Client::builder()
                .timeout(std::time::Duration::from_secs(30))
                .build()?,
            cache_dir: cache,
        })
    }

    pub fn discover(&self, job: &Job) -> Result<Vec<Asset>> {
        let parsed = Url::parse(&job.source)?;
        if parsed.scheme() != "http" && parsed.scheme() != "https" {
            return Ok(vec![]);
        }

        let size_hint = self.head_size(&parsed).unwrap_or(None);
        let inline_allowed = size_hint
            .map(|size| size <= INLINE_THRESHOLD)
            .unwrap_or(false);

        let (path, mime, meta) = if inline_allowed {
            let mut response = self.client.get(parsed.clone()).send()?;
            let mime = response
                .headers()
                .get(CONTENT_TYPE)
                .and_then(|value| value.to_str().ok())
                .map(|s| s.to_string());
            let bytes = read_all(&mut response)?;
            let cache_key = cache_key(parsed.as_str());
            let dest = self.cache_dir.join(format!(
                "{cache_key}{}",
                guess_suffix(&parsed, mime.as_deref())
            ));
            let encoded = BASE64.encode(&bytes);
            ensure_dir(dest.parent().unwrap())?;
            File::create(&dest)?.write_all(&bytes)?;
            let meta = serde_json::json!({
                "url": job.source,
                "size_bytes": bytes.len(),
                "inline_bytes": encoded,
                "upload_cache_key": cache_key,
            });
            (dest, mime, meta)
        } else {
            let mut response = self.client.get(parsed.clone()).send()?;
            let mime = response
                .headers()
                .get(CONTENT_TYPE)
                .and_then(|value| value.to_str().ok())
                .map(|s| s.to_string());
            let target = self.cache_dir.join(format!(
                "{}{}",
                cache_key(parsed.as_str()),
                guess_suffix(&parsed, mime.as_deref())
            ));
            ensure_dir(target.parent().unwrap())?;
            let mut file = File::create(&target)?;
            let size = copy(&mut response, &mut file)? as usize;
            let meta = serde_json::json!({
                "url": job.source,
                "size_bytes": size,
                "upload_cache_key": cache_key(parsed.as_str()),
            });
            (target, mime, meta)
        };

        let media = infer_media(&parsed, mime.as_deref());
        if media.is_none() {
            return Ok(vec![]);
        }
        Ok(vec![Asset {
            path,
            media: media.unwrap().to_string(),
            page_index: None,
            source_kind: SourceKind::Url,
            mime,
            meta,
        }])
    }

    fn head_size(&self, url: &Url) -> Result<Option<usize>> {
        let response = self.client.head(url.clone()).send();
        match response {
            Ok(resp) => Ok(resp
                .headers()
                .get(CONTENT_LENGTH)
                .and_then(|value| value.to_str().ok())
                .and_then(|value| value.parse().ok())),
            Err(_) => Ok(None),
        }
    }
}

fn read_all(response: &mut Response) -> Result<Vec<u8>> {
    let mut bytes = Vec::new();
    response.read_to_end(&mut bytes)?;
    Ok(bytes)
}

fn guess_suffix(url: &Url, mime: Option<&str>) -> String {
    if let Some(extension) = Path::new(url.path()).extension() {
        return format!(".{}", extension.to_string_lossy());
    }
    if let Some(mime_type) = mime {
        if let Ok(parsed) = mime::Mime::from_str(mime_type) {
            if let Some(exts) = mime_guess::get_mime_extensions(&parsed) {
                if let Some(ext) = exts.first() {
                    return format!(".{ext}");
                }
            }
        }
    }
    String::new()
}

fn infer_media(url: &Url, mime: Option<&str>) -> Option<&'static str> {
    if let Some(mime) = mime {
        match mime {
            "application/pdf" => return Some("pdf"),
            "image/png" | "image/jpeg" | "image/gif" | "image/tiff" => return Some("image"),
            "video/mp4" => return Some("video"),
            "audio/mpeg" => return Some("audio"),
            _ => {}
        }
    }
    Path::new(url.path())
        .extension()
        .and_then(|ext| match ext.to_str()?.to_lowercase().as_str() {
            "pdf" => Some("pdf"),
            "png" | "jpg" | "jpeg" | "gif" | "tif" | "tiff" | "bmp" => Some("image"),
            "mp4" | "mov" | "mkv" => Some("video"),
            "mp3" | "wav" | "m4a" => Some("audio"),
            _ => None,
        })
}

fn cache_key(url: &str) -> String {
    format!("{:x}", Sha256::digest(url.as_bytes()))
}
