pub fn slide_preamble() -> &'static str {
    include_str!("../templates/slide-template.txt")
}

pub fn lecture_preamble() -> &'static str {
    include_str!("../templates/lecture-template.txt")
}

pub fn document_preamble() -> &'static str {
    include_str!("../templates/document-template.txt")
}

pub fn image_preamble() -> &'static str {
    include_str!("../templates/image-template.txt")
}

pub fn video_preamble() -> &'static str {
    include_str!("../templates/video-template.txt")
}

pub fn default_prompt(kind: &str, preamble: &str) -> String {
    match kind {
        "slides" => format!("{preamble}\nSummarize slide content. Preserve order and math."),
        "lecture" => format!("{preamble}\nProduce a lecture summary with [MM:SS] timestamps."),
        "image" => format!("{preamble}\nDescribe the image and transcribe text."),
        "video" => format!("{preamble}\nTranscript with [MM:SS] and visual events."),
        _ => format!("{preamble}\nSummarize the document. Keep math."),
    }
}
