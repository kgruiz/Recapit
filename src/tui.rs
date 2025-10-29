use crossterm::{
    cursor,
    event::{self, Event, KeyCode, KeyEventKind, KeyModifiers},
    execute, queue,
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
        queue!(
            out,
            cursor::MoveTo(0, base_row),
            Clear(ClearType::FromCursorDown)
        )?;
        writeln!(out, "progress:")?;
        for task in &order {
            if let Some(state) = rows.get(task) {
                let percent = if state.total > 0 {
                    (state.cur as f64 / state.total as f64).min(1.0)
                } else {
                    0.0
                };
                let bar = format!("{:>3}%", (percent * 100.0) as u64);
                let line = format!(
                    "{task:10}  [{:50}]  {bar}  {}",
                    progress_bar(percent),
                    state.status
                );
                writeln!(out, "{line}")?;
            }
        }
        out.flush()?;

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

        if closed && rows.values().all(|state| state.cur >= state.total) {
            break;
        }
    }

    terminal::disable_raw_mode()?;
    let lines = order.len() as u16 + 2;
    execute!(out, cursor::MoveTo(0, base_row + lines), cursor::Show)?;
    writeln!(out)?;
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
