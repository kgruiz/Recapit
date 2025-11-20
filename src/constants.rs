//! Shared constants for the recapit engine.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

pub const GEMINI_2_5_FLASH: &str = "gemini-2.5-flash";
pub const GEMINI_2_5_FLASH_LITE: &str = "gemini-2.5-flash-lite";
pub const GEMINI_2_5_PRO: &str = "gemini-2.5-pro";
pub const GEMINI_3_PRO_PREVIEW: &str = "gemini-3-pro-preview";

pub fn model_capabilities() -> HashMap<&'static str, &'static [&'static str]> {
    HashMap::from([
        (GEMINI_2_5_FLASH, &["text", "image", "audio", "video"][..]),
        (
            GEMINI_2_5_FLASH_LITE,
            &["text", "image", "audio", "video", "pdf"][..],
        ),
        (
            GEMINI_2_5_PRO,
            &["text", "image", "audio", "video", "pdf"][..],
        ),
        (
            GEMINI_3_PRO_PREVIEW,
            &["text", "image", "audio", "video", "pdf"][..],
        ),
    ])
}

pub fn rate_limits_per_minute() -> HashMap<&'static str, u32> {
    HashMap::from([
        (GEMINI_3_PRO_PREVIEW, 50),
        (GEMINI_2_5_PRO, 150),
        (GEMINI_2_5_FLASH, 1_000),
        (GEMINI_2_5_FLASH_LITE, 4_000),
    ])
}

pub fn token_limits_per_minute() -> HashMap<&'static str, u32> {
    HashMap::from([
        (GEMINI_3_PRO_PREVIEW, 1_000_000),
        (GEMINI_2_5_PRO, 2_000_000),
        (GEMINI_2_5_FLASH, 1_000_000),
        (GEMINI_2_5_FLASH_LITE, 4_000_000),
    ])
}

pub fn default_model_pricing() -> HashMap<&'static str, ModelPricing> {
    HashMap::from([
        (
            GEMINI_2_5_PRO,
            ModelPricing::new((3.50, 10.00), (3.00, 15.00)),
        ),
        (
            GEMINI_2_5_FLASH,
            ModelPricing::new((0.35, 1.05), (0.70, 2.10)),
        ),
        (
            GEMINI_2_5_FLASH_LITE,
            ModelPricing::new((0.10, 0.40), (0.30, 1.20)),
        ),
        (
            GEMINI_3_PRO_PREVIEW,
            ModelPricing::new((4.00, 18.00), (4.00, 18.00)),
        ),
        ("default", ModelPricing::new((0.0, 0.0), (0.0, 0.0))),
    ])
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelPricing {
    pub text: PricePair,
    pub audio_video: PricePair,
}

impl ModelPricing {
    const fn new(text: (f64, f64), audio_video: (f64, f64)) -> Self {
        Self {
            text: PricePair::new(text.0, text.1),
            audio_video: PricePair::new(audio_video.0, audio_video.1),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PricePair {
    pub input: f64,
    pub output: f64,
}

impl PricePair {
    const fn new(input: f64, output: f64) -> Self {
        Self { input, output }
    }
}

pub const DEFAULT_MODEL: &str = GEMINI_3_PRO_PREVIEW;
pub const DEFAULT_VIDEO_TOKEN_LIMIT: u32 = 300_000;
pub const DEFAULT_VIDEO_TOKENS_PER_SECOND: f64 = 300.0;
pub const DEFAULT_MAX_WORKERS: usize = 4;
pub const DEFAULT_MAX_VIDEO_WORKERS: usize = 3;
pub const DEFAULT_PDF_DPI: u32 = 200;
