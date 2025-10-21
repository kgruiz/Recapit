use crossterm::{event, execute, terminal};
use ratatui::{prelude::*, widgets::*};
use std::collections::HashMap;
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

    loop {
        terminal.draw(|frame| {
            let size = frame.size();
            let block = Block::default().title("recapit").borders(Borders::ALL);
            let inner = block.inner(size);
            frame.render_widget(block, size);

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
            frame.render_widget(list, inner);
        })?;

        if event::poll(std::time::Duration::from_millis(33))? {
            if let event::Event::Key(key) = event::read()? {
                if key.code == event::KeyCode::Char('q') {
                    break;
                }
            }
        }

        while let Ok(evt) = rx.try_recv() {
            let entry = rows.entry(evt.task.clone()).or_default();
            entry.kind = evt.kind;
            entry.cur = evt.current;
            entry.total = evt.total.max(1);
            entry.status = evt.status;
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
