use crossterm::{
    cursor,
    event::{self, Event, KeyCode, KeyEventKind, KeyModifiers},
    execute, queue,
    style::{Color, PrintStyledContent, Stylize},
    terminal::{self, Clear, ClearType},
};
use std::collections::HashMap;
use std::io::{stdout, Write};
use tokio::sync::mpsc::error::TryRecvError;
use tokio::sync::mpsc::{UnboundedReceiver, UnboundedSender};

use crate::progress::{Progress, ProgressScope, ProgressStage};

struct RowState {
    stage: ProgressStage,
    cur: u64,
    total: u64,
    status: String,
    finished_at: Option<std::time::Instant>,
}

pub async fn run_tui(
    mut rx: UnboundedReceiver<Progress>,
    cancel: UnboundedSender<()>,
) -> anyhow::Result<()> {
    let mut out = stdout();
    let (col, mut row) = cursor::position()?;
    if col != 0 {
        writeln!(out)?;
        out.flush()?;
        let pos = cursor::position()?;
        row = pos.1;
    }
    terminal::enable_raw_mode()?;
    execute!(out, cursor::Hide)?;

    let base_row = row;
    let mut rows: HashMap<ProgressScope, RowState> = HashMap::new();
    let mut order: Vec<ProgressScope> = Vec::new();
    let mut closed = false;
    let frames = ["|", "/", "-", "\\"];
    let mut frame_idx: usize = 0;

    loop {
        loop {
            match rx.try_recv() {
                Ok(evt) => {
                    let key = evt.scope.clone();
                    let entry = rows.entry(key.clone()).or_insert(RowState {
                        stage: evt.stage,
                        cur: 0,
                        total: 1,
                        status: String::new(),
                        finished_at: None,
                    });
                    if !order.contains(&key) {
                        order.push(key.clone());
                    }
                    entry.stage = evt.stage;
                    entry.cur = evt.current.min(evt.total.max(1));
                    entry.total = evt.total.max(1);
                    entry.status = evt.status;
                    if evt.finished {
                        entry.finished_at = Some(std::time::Instant::now());
                    }
                }
                Err(TryRecvError::Empty) => break,
                Err(TryRecvError::Disconnected) => {
                    closed = true;
                    break;
                }
            }
        }

        queue!(
            out,
            cursor::MoveTo(0, base_row),
            Clear(ClearType::FromCursorDown)
        )?;

        frame_idx = (frame_idx + 1) % frames.len();

        // Trim finished rows after a short delay.
        let now = std::time::Instant::now();
        rows.retain(|_, state| match state.finished_at {
            Some(done) => now.duration_since(done).as_millis() < 1200,
            None => true,
        });
        order.retain(|scope| rows.contains_key(scope));

        // Determine whether to show the run bar when there is only one job and no chunk bars.
        let job_count = rows
            .keys()
            .filter(|s| matches!(s, ProgressScope::Job { .. }))
            .count();
        let chunk_progress_count = rows
            .keys()
            .filter(|s| matches!(s, ProgressScope::ChunkProgress { .. }))
            .count();
        let chunk_detail_count = rows
            .keys()
            .filter(|s| matches!(s, ProgressScope::ChunkDetail { .. }))
            .count();

        let start_row = base_row;
        let cols = terminal::size().map(|(c, _)| c as usize).unwrap_or(80);
        let mut render_idx = 0;
        for scope in order.clone() {
            if let Some(state) = rows.get(&scope) {
                if matches!(scope, ProgressScope::Run)
                    && job_count == 1
                    && chunk_progress_count == 0
                    && chunk_detail_count == 0
                {
                    // Collapse run bar when single job/chunk to show only one bar.
                    continue;
                }

                let percent = if state.total > 0 {
                    (state.cur as f64 / state.total as f64).min(1.0)
                } else {
                    0.0
                };
                let percent_label = format!("{:>3}%", (percent * 100.0).round() as u64);

                let count_label = if state.total > 0 {
                    format!("{:>5}/{:<5}", state.cur.min(state.total), state.total)
                } else {
                    "  -/- ".to_string()
                };

                let label_text = if !matches!(scope, ProgressScope::Run) {
                    format!("{} · {}", scope, state.stage.label())
                } else {
                    scope.to_string()
                };

                let spin = if percent >= 1.0 {
                    " "
                } else {
                    frames[frame_idx]
                };

                let min_bar_width = 10;
                let base_len = 2 /*spin+space*/
                    + label_text.len()
                    + 2 /*leading space+bracket*/
                    + 2 /*trailing bracket+space*/
                    + percent_label.len()
                    + 1 /*space*/
                    + count_label.len()
                    + 1; /*space before status*/

                let available = cols.saturating_sub(base_len);

                let mut status_text = state.status.clone();

                if available <= min_bar_width {
                    status_text.clear();
                } else {
                    let max_status_len = available - min_bar_width;

                    if status_text.len() > max_status_len {
                        status_text = truncate_status(&status_text, max_status_len);
                    }
                }

                let status_len = status_text.len();
                let bar_width = available.saturating_sub(status_len).max(1);
                let bar = progress_bar(percent, bar_width);
                let styled_bar = if percent >= 1.0 {
                    bar.clone().with(Color::Green)
                } else {
                    bar.clone().with(Color::Yellow)
                };
                let status_style = if percent >= 1.0 {
                    status_text.clone().with(Color::Green)
                } else {
                    status_text.clone().with(Color::White)
                };
                queue!(
                    out,
                    cursor::MoveTo(0, start_row + render_idx as u16),
                    Clear(ClearType::CurrentLine),
                    PrintStyledContent(format!("{spin} {label_text} ").with(Color::White)),
                    PrintStyledContent(" [".with(Color::DarkGrey)),
                    PrintStyledContent(styled_bar),
                    PrintStyledContent("] ".with(Color::DarkGrey)),
                    PrintStyledContent(percent_label.with(Color::Cyan)),
                    PrintStyledContent(" ".with(Color::DarkGrey)),
                    PrintStyledContent(count_label.with(Color::Magenta)),
                    PrintStyledContent(" ".with(Color::DarkGrey)),
                    PrintStyledContent(status_style)
                )?;
                render_idx += 1;
            }
        }
        queue!(
            out,
            cursor::MoveTo(0, start_row + render_idx as u16),
            Clear(ClearType::CurrentLine)
        )?;
        out.flush()?;

        if closed && rows.values().all(|state| state.cur >= state.total) {
            break;
        }

        if event::poll(std::time::Duration::from_millis(33))? {
            if let Event::Key(key) = event::read()? {
                if key.kind == KeyEventKind::Press
                    && (key.code == KeyCode::Char('q')
                        || (key.code == KeyCode::Char('c')
                            && key.modifiers.contains(KeyModifiers::CONTROL)))
                {
                    let _ = cancel.send(());
                    break;
                }
            }
        }
    }

    terminal::disable_raw_mode()?;
    let (_, term_rows) = terminal::size().unwrap_or((80, 24));
    let current_row = cursor::position().map(|(_, r)| r).unwrap_or(base_row);
    let mut final_row = current_row.saturating_add(1);

    if final_row >= term_rows {
        final_row = term_rows.saturating_sub(1);
    }

    execute!(
        out,
        cursor::MoveTo(0, final_row),
        Clear(ClearType::CurrentLine),
        cursor::Show,
        cursor::MoveToNextLine(1)
    )?;
    out.flush()?;
    Ok(())
}

fn progress_bar(progress: f64, width: usize) -> String {
    let filled = (progress * width as f64).round() as usize;
    let mut bar = String::with_capacity(width);
    for idx in 0..width {
        bar.push(if idx < filled { '#' } else { ' ' });
    }
    bar
}

fn truncate_status(status: &str, max_len: usize) -> String {
    if status.len() <= max_len {
        return status.to_string();
    }

    if max_len <= 3 {
        return status.chars().take(max_len).collect();
    }

    let keep_len = max_len - 3;
    let mut truncated: String = status.chars().take(keep_len).collect();
    truncated.push_str("...");
    truncated
}
