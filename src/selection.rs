use anyhow::{anyhow, Result};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IndexSelection {
    spans: Vec<IndexSpan>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct IndexSpan {
    start: Option<u32>,
    end: Option<u32>,
}

impl IndexSelection {
    pub fn parse(input: &str) -> Result<Self> {
        let trimmed = input.trim();
        if trimmed.is_empty() {
            return Err(anyhow!("page selection is empty"));
        }

        if matches!(trimmed, "*" | "all") {
            return Ok(Self {
                spans: vec![IndexSpan {
                    start: None,
                    end: None,
                }],
            });
        }

        let mut spans = Vec::new();
        for token in trimmed.split(',') {
            let token = token.trim();
            if token.is_empty() {
                return Err(anyhow!("page selection contains an empty segment"));
            }

            if token == "-" {
                spans.push(IndexSpan {
                    start: None,
                    end: None,
                });
                continue;
            }

            if let Some((lhs, rhs)) = token.split_once('-') {
                let start = parse_optional_index(lhs)?;
                let end = parse_optional_index(rhs)?;

                if let (Some(start), Some(end)) = (start, end) {
                    if start > end {
                        return Err(anyhow!(
                            "invalid page range '{token}': start {start} exceeds end {end}"
                        ));
                    }
                }

                spans.push(IndexSpan { start, end });
            } else {
                let value: u32 = token
                    .parse()
                    .map_err(|_| anyhow!("invalid page index '{token}'"))?;
                if value == 0 {
                    return Err(anyhow!("page indexes are 1-based; got 0"));
                }
                spans.push(IndexSpan {
                    start: Some(value),
                    end: Some(value),
                });
            }
        }

        Ok(Self { spans })
    }

    pub fn merged_ranges(&self, total: u32) -> Result<Vec<(u32, u32)>> {
        if total == 0 {
            return Err(anyhow!("total page count must be positive"));
        }

        let mut ranges: Vec<(u32, u32)> = Vec::new();
        for span in &self.spans {
            let start = span.start.unwrap_or(1);
            let end = span.end.unwrap_or(total);

            if start == 0 || end == 0 {
                return Err(anyhow!("page indexes are 1-based; got 0"));
            }
            if start > total {
                return Err(anyhow!(
                    "page selection start {start} exceeds document total {total}"
                ));
            }
            if end > total {
                return Err(anyhow!(
                    "page selection end {end} exceeds document total {total}"
                ));
            }
            if start > end {
                continue;
            }

            ranges.push((start, end));
        }

        if ranges.is_empty() {
            return Err(anyhow!("page selection resolves to no pages"));
        }

        ranges.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.cmp(&b.1)));

        let mut merged: Vec<(u32, u32)> = Vec::new();
        for (start, end) in ranges {
            if let Some((last_start, last_end)) = merged.last_mut() {
                if start <= *last_end + 1 {
                    *last_end = (*last_end).max(end);
                    *last_start = (*last_start).min(start);
                    continue;
                }
            }
            merged.push((start, end));
        }

        Ok(merged)
    }

    pub fn is_full(&self) -> bool {
        self.spans
            .iter()
            .any(|span| span.start.is_none() && span.end.is_none())
    }
}

impl std::fmt::Display for IndexSelection {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        if self.is_full() {
            return write!(f, "all");
        }

        let parts = self
            .spans
            .iter()
            .map(|span| match (span.start, span.end) {
                (Some(start), Some(end)) if start == end => start.to_string(),
                (start, end) => format!(
                    "{}-{}",
                    start.map(|v| v.to_string()).unwrap_or_default(),
                    end.map(|v| v.to_string()).unwrap_or_default()
                ),
            })
            .collect::<Vec<_>>();
        write!(f, "{}", parts.join(","))
    }
}

fn parse_optional_index(input: &str) -> Result<Option<u32>> {
    let trimmed = input.trim();
    if trimmed.is_empty() {
        return Ok(None);
    }
    let value: u32 = trimmed
        .parse()
        .map_err(|_| anyhow!("invalid page index '{trimmed}'"))?;
    if value == 0 {
        return Err(anyhow!("page indexes are 1-based; got 0"));
    }
    Ok(Some(value))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_single_pages_and_ranges() {
        let selection = IndexSelection::parse("1,3-5,10-").unwrap();
        assert_eq!(
            selection.merged_ranges(12).unwrap(),
            vec![(1, 1), (3, 5), (10, 12)]
        );
    }

    #[test]
    fn merges_overlapping_ranges() {
        let selection = IndexSelection::parse("1-3,3-4,6,5-7").unwrap();
        assert_eq!(selection.merged_ranges(10).unwrap(), vec![(1, 7)]);
    }

    #[test]
    fn rejects_zero() {
        assert!(IndexSelection::parse("0").is_err());
        assert!(IndexSelection::parse("1-0").is_err());
    }
}
