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
use tokio::sync::mpsc::UnboundedReceiver;

use crate::engine::{Progress, ProgressKind};

struct RowState {
    kind: ProgressKind,
    cur: u64,
    total: u64,
    status: String,
}

impl Default for RowState {
    fn default() -> Self {
        Self {
            kind: ProgressKind::Discover,
            cur: 0,
            total: 1,
            status: String::new(),
        }
    }
}

pub async fn run_tui(mut rx: UnboundedReceiver<Progress>) -> anyhow::Result<()> {
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
    let mut rows: HashMap<String, RowState> = HashMap::new();
    let mut order: Vec<String> = Vec::new();
    let mut closed = false;

    loop {
        loop {
            match rx.try_recv() {
                Ok(evt) => {
                    let entry = rows.entry(evt.task.clone()).or_default();
                    if !order.contains(&evt.task) {
                        order.push(evt.task.clone());
                    }
                    entry.kind = evt.kind;
                    entry.cur = evt.current;
                    entry.total = evt.total.max(1);
                    entry.status = evt.status;
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
            Clear(ClearType::FromCursorDown),
            PrintStyledContent("progress:".with(Color::DarkGrey))
        )?;
        let start_row = base_row + 1;
        for (idx, task) in order.iter().enumerate() {
            if let Some(state) = rows.get(task) {
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
                let bar = progress_bar(percent);
                let styled_bar = if percent >= 1.0 {
                    bar.clone().with(Color::Green)
                } else {
                    bar.clone().with(Color::Yellow)
                };
                let status_style = if percent >= 1.0 {
                    state.status.clone().with(Color::Green)
                } else {
                    state.status.clone().with(Color::White)
                };
                queue!(
                    out,
                    cursor::MoveTo(0, start_row + idx as u16),
                    Clear(ClearType::CurrentLine),
                    PrintStyledContent(format!("{task:10}  ").with(Color::White)),
                    PrintStyledContent("[".with(Color::DarkGrey)),
                    PrintStyledContent(styled_bar),
                    PrintStyledContent("]  ".with(Color::DarkGrey)),
                    PrintStyledContent(percent_label.with(Color::Cyan)),
                    PrintStyledContent("  ".with(Color::DarkGrey)),
                    PrintStyledContent(count_label.with(Color::Magenta)),
                    PrintStyledContent("  ".with(Color::DarkGrey)),
                    PrintStyledContent(status_style)
                )?;
            }
        }
        queue!(
            out,
            cursor::MoveTo(0, start_row + order.len() as u16),
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
                    break;
                }
            }
        }
    }

    terminal::disable_raw_mode()?;
    let final_row = base_row + 1 + order.len() as u16;
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

fn progress_bar(progress: f64) -> String {
    let width = 50usize;
    let filled = (progress * width as f64).round() as usize;
    let mut bar = String::with_capacity(width);
    for idx in 0..width {
        bar.push(if idx < filled { '#' } else { ' ' });
    }
    bar
}
