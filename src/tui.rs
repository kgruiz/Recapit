use crossterm::{
    event::{self, Event, KeyCode, KeyEventKind, KeyModifiers},
    execute, terminal,
};
use ratatui::{prelude::*, widgets::*};
use std::collections::HashMap;
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
    let mut stdout = std::io::stdout();
    terminal::enable_raw_mode()?;
    execute!(
        stdout,
        terminal::EnterAlternateScreen,
        event::EnableMouseCapture
    )?;
    let mut terminal = Terminal::new(CrosstermBackend::new(stdout))?;
    let mut rows: HashMap<String, RowState> = HashMap::new();
    let mut closed = false;

    loop {
        terminal.draw(|frame| {
            let area = frame.size();
            let mut items = Vec::new();
            for (task, state) in rows.iter() {
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
                items.push(ListItem::new(line));
            }
            let list =
                List::new(items).block(Block::default().title("progress").borders(Borders::ALL));
            let min_height = 3u16;
            let content_height = (rows.len() as u16).saturating_add(2).max(min_height);
            let height = content_height.min(area.height);
            let list_area = Rect::new(area.x, area.y, area.width, height);
            frame.render_widget(list, list_area);
        })?;

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
    execute!(
        terminal.backend_mut(),
        terminal::LeaveAlternateScreen,
        event::DisableMouseCapture
    )?;
    terminal.show_cursor()?;
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
