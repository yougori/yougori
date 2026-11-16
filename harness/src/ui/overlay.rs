//! The screen's own panels, drawn at the bottom in the CLI's menu style (◆ title, │ rails,
//! ● and ○ choices, └ key hints): the command palette, the model and mode pickers, the /harness
//! panel (switcher.rs), confirmations and the keys help.
use super::input::Editor;
use super::switcher::{Flow, FlowOutcome, Pending};
use super::text::{self, pad, span, truncate, truncate_spans, wrap_text};
use super::theme::Theme;
use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui::style::Style;
use ratatui::text::{Line, Span};

/// What a palette entry does.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Action {
    /// The /harness panel.
    Harness,
    Model,
    Mode,
    /// A slash command sent to the engine (without the slash).
    Slash(String),
    Details,
    Keys,
    Quit,
    /// Sends Ctrl+P to a terminal pane's program.
    SendCtrlP,
    /// Starts a stopped engine again.
    Restart,
    /// A new chat (/new); the one before is kept.
    NewChat,
    /// The chats kept (/history).
    History,
}

#[derive(Clone, Debug)]
pub struct Item {
    pub action: Action,
    pub label: String,
    pub hint: String,
    pub enabled: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Confirmable {
    Quit,
    SwitchModel(String),
    /// /new in a terminal pane: its client stops and starts fresh.
    Renew,
}

/// One of Yougori's commands in a terminal pane: its name, what it does, what to type after it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Command {
    pub name: String,
    pub about: String,
    pub takes: Option<String>,
}

#[derive(Clone, Debug)]
pub struct Choice {
    pub id: String,
    pub label: String,
    pub hint: String,
    pub current: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Which {
    Models,
    Modes,
    /// /history's chats.
    Chats,
    /// How to sign in to the plan whose model (this id) was chosen.
    Plan(String),
}

pub enum Overlay {
    Palette { filter: Editor, items: Vec<Item>, selected: usize },
    /// /harness: the harnesses, the chosen one's questions, a new VM's confirmation.
    Switch(Box<Flow>),
    Picker { which: Which, title: String, choices: Vec<Choice>, selected: usize },
    Confirm { what: Confirmable, title: String, detail: String, yes: String },
    Keys { keys: Vec<(String, String)>, scroll: usize },
    /// A `/` typed at the start of a terminal pane's line: Yougori's commands, while what is typed
    /// still names one; `client` is the program the rest goes to.
    Command { line: Editor, commands: Vec<Command>, selected: usize, client: String },
}

#[derive(Debug, PartialEq)]
pub enum Outcome {
    Stay,
    Close,
    Run(Action),
    Pick(Which, String),
    Confirmed(Confirmable),
    /// /harness chose a harness and its answers: the switch starts.
    Switch(Box<Pending>),
    /// One of a terminal pane's commands (name, what was typed after it).
    Command(String, String),
    /// What was typed after `/` names none of Yougori's commands: it goes to the client as typed.
    Pass(String),
}

/// Whether `text` still names one of `commands`: `/` and the start of a name, or a whole name
/// that takes text, a space and that text.
pub fn names_command(commands: &[Command], text: &str) -> bool {
    let Some(rest) = text.strip_prefix('/') else { return false };
    match rest.split_once(char::is_whitespace) {
        Some((name, _)) => commands.iter().any(|c| c.takes.is_some() && c.name.eq_ignore_ascii_case(name)),
        None => {
            let typed = rest.to_lowercase();
            commands.iter().any(|c| c.name.to_lowercase().starts_with(&typed))
        }
    }
}

/// The commands `text` names: those whose name starts with what is typed, or the one named whole
/// before the text it takes.
fn named(commands: &[Command], text: &str) -> Vec<usize> {
    let rest = text.strip_prefix('/').unwrap_or(text);
    match rest.split_once(char::is_whitespace) {
        Some((name, _)) => (0..commands.len()).filter(|&i| commands[i].name.eq_ignore_ascii_case(name)).collect(),
        None => {
            let typed = rest.to_lowercase();
            (0..commands.len()).filter(|&i| commands[i].name.to_lowercase().starts_with(&typed)).collect()
        }
    }
}

impl Overlay {
    pub fn palette(items: Vec<Item>) -> Overlay {
        let selected = items.iter().position(|i| i.enabled).unwrap_or(0);
        Overlay::Palette { filter: Editor::default(), items, selected }
    }

    pub fn picker(which: Which, title: &str, choices: Vec<Choice>) -> Overlay {
        let selected = choices.iter().position(|c| c.current).unwrap_or(0);
        Overlay::Picker { which, title: title.to_owned(), choices, selected }
    }

    /// The `/` just typed in a terminal pane, with Yougori's commands for `client`.
    pub fn command(commands: Vec<Command>, client: &str) -> Overlay {
        let mut line = Editor::default();
        line.insert("/");
        Overlay::Command { line, commands, selected: 0, client: client.to_owned() }
    }

    /// Text pasted into the panel: the palette's filter, the field /harness is typing, or a
    /// command's text.
    pub fn paste(&mut self, text: &str) {
        match self {
            Overlay::Palette { filter, items, selected } => {
                let line: String = text.chars().filter(|c| !c.is_control()).collect();
                filter.insert(&line);
                reselect(selected, items, filter.text());
            }
            Overlay::Switch(flow) => flow.paste(text),
            Overlay::Command { line, selected, .. } => {
                let pasted: String = text.chars().filter(|c| !c.is_control()).collect();
                line.insert(&pasted);
                *selected = 0;
            }
            Overlay::Picker { .. } | Overlay::Confirm { .. } | Overlay::Keys { .. } => {}
        }
    }

    /// A command panel whose text names none of its commands any more: that text, for the client.
    pub fn passing(&self) -> Option<String> {
        match self {
            Overlay::Command { line, commands, .. } if !names_command(commands, line.text()) => {
                Some(line.text().to_owned())
            }
            _ => None,
        }
    }

    pub fn key(&mut self, key: KeyEvent) -> Outcome {
        let control = key.modifiers.contains(KeyModifiers::CONTROL);
        let alt = key.modifiers.contains(KeyModifiers::ALT);
        match self {
            Overlay::Command { line, commands, selected, .. } => {
                let shown = named(commands, line.text());
                match key.code {
                    // Esc leaves what is typed to the client, as Esc in the chat leaves it in the box.
                    KeyCode::Esc => return Outcome::Pass(line.text().to_owned()),
                    KeyCode::Char('c' | 'u') if control => return Outcome::Close,
                    KeyCode::Backspace if line.text() == "/" => return Outcome::Close,
                    KeyCode::Char('h') if control && line.text() == "/" => return Outcome::Close,
                    KeyCode::Backspace => line.backspace(),
                    // Backspace as the Windows console gives it.
                    KeyCode::Char('h') if control => line.backspace(),
                    KeyCode::Up | KeyCode::BackTab => {
                        if !shown.is_empty() {
                            *selected = (*selected + shown.len() - 1) % shown.len();
                        }
                        return Outcome::Stay;
                    }
                    KeyCode::Down | KeyCode::Tab => {
                        if !shown.is_empty() {
                            *selected = (*selected + 1) % shown.len();
                        }
                        return Outcome::Stay;
                    }
                    KeyCode::Enter => {
                        let text = line.text().trim().to_owned();
                        let rest = text.strip_prefix('/').unwrap_or(&text);
                        let (name, argument) = match rest.split_once(char::is_whitespace) {
                            Some((name, argument)) => (name, argument.trim()),
                            None => (rest, ""),
                        };
                        if let Some(command) = commands.iter().find(|c| c.name.eq_ignore_ascii_case(name)) {
                            return Outcome::Command(command.name.clone(), argument.to_owned());
                        }
                        // A name typed in part: the chosen command, as Enter in the chat's list
                        // chooses it (one that takes text waits for it).
                        let Some(&chosen) = shown.get((*selected).min(shown.len().saturating_sub(1))) else {
                            return Outcome::Pass(format!("{}\r", line.text()));
                        };
                        let command = &commands[chosen];
                        if command.takes.is_some() {
                            line.set(&format!("/{} ", command.name));
                            *selected = 0;
                            return Outcome::Stay;
                        }
                        return Outcome::Command(command.name.clone(), String::new());
                    }
                    KeyCode::Left => line.left(),
                    KeyCode::Right => line.right(),
                    KeyCode::Home => line.home(),
                    KeyCode::End => line.end(),
                    KeyCode::Delete => line.delete(),
                    KeyCode::Char(c) if !control && !alt => line.insert(&c.to_string()),
                    _ => return Outcome::Stay,
                }
                // The `/` itself deleted: no command, and nothing for the client.
                if !line.text().starts_with('/') {
                    return Outcome::Close;
                }
                if !names_command(commands, line.text()) {
                    return Outcome::Pass(line.text().to_owned());
                }
                *selected = (*selected).min(named(commands, line.text()).len().saturating_sub(1));
                Outcome::Stay
            }
            Overlay::Switch(flow) => match flow.key(key) {
                FlowOutcome::Stay => Outcome::Stay,
                FlowOutcome::Close => Outcome::Close,
                FlowOutcome::Start(pending) => Outcome::Switch(pending),
            },
            Overlay::Palette { filter, items, selected } => {
                let visible: Vec<usize> = matching(items, filter.text());
                match key.code {
                    KeyCode::Esc => return Outcome::Close,
                    KeyCode::Char('c') if control => return Outcome::Close,
                    KeyCode::Char('p') if control => return Outcome::Close,
                    KeyCode::Enter => {
                        return match visible.iter().find(|i| **i == *selected).or(visible.first()) {
                            Some(&i) if items[i].enabled => Outcome::Run(items[i].action.clone()),
                            _ => Outcome::Stay,
                        };
                    }
                    KeyCode::Up | KeyCode::BackTab => step(selected, &visible, items, -1),
                    KeyCode::Down | KeyCode::Tab => step(selected, &visible, items, 1),
                    KeyCode::Char('n') if control => step(selected, &visible, items, 1),
                    KeyCode::Char('k') if control => step(selected, &visible, items, -1),
                    KeyCode::Backspace => {
                        filter.backspace();
                        reselect(selected, items, filter.text());
                    }
                    KeyCode::Char('u') if control => {
                        filter.clear();
                        reselect(selected, items, filter.text());
                    }
                    KeyCode::Char(c) if !control => {
                        filter.insert(&c.to_string());
                        reselect(selected, items, filter.text());
                    }
                    _ => {}
                }
                Outcome::Stay
            }
            Overlay::Picker { which, choices, selected, .. } => {
                if choices.is_empty() {
                    return if matches!(key.code, KeyCode::Esc | KeyCode::Enter) {
                        Outcome::Close
                    } else {
                        Outcome::Stay
                    };
                }
                match key.code {
                    KeyCode::Esc => Outcome::Close,
                    KeyCode::Char('c') if control => Outcome::Close,
                    KeyCode::Up | KeyCode::BackTab => {
                        *selected = (*selected + choices.len() - 1) % choices.len();
                        Outcome::Stay
                    }
                    KeyCode::Down | KeyCode::Tab => {
                        *selected = (*selected + 1) % choices.len();
                        Outcome::Stay
                    }
                    KeyCode::Home => {
                        *selected = 0;
                        Outcome::Stay
                    }
                    KeyCode::End => {
                        *selected = choices.len() - 1;
                        Outcome::Stay
                    }
                    KeyCode::Char(c @ '1'..='9') if !control => {
                        let i = c as usize - '1' as usize;
                        match choices.get(i) {
                            Some(choice) => Outcome::Pick(which.clone(), choice.id.clone()),
                            None => Outcome::Stay,
                        }
                    }
                    KeyCode::Enter => Outcome::Pick(which.clone(), choices[*selected].id.clone()),
                    _ => Outcome::Stay,
                }
            }
            Overlay::Confirm { what, .. } => match key.code {
                KeyCode::Enter | KeyCode::Char('y') | KeyCode::Char('Y') => Outcome::Confirmed(what.clone()),
                KeyCode::Esc | KeyCode::Char('n') | KeyCode::Char('N') => Outcome::Close,
                KeyCode::Char('c') if control => Outcome::Close,
                _ => Outcome::Stay,
            },
            Overlay::Keys { keys, scroll } => match key.code {
                KeyCode::Esc | KeyCode::Enter | KeyCode::Char('q') => Outcome::Close,
                KeyCode::Char('c') if control => Outcome::Close,
                KeyCode::Up => {
                    *scroll = scroll.saturating_sub(1);
                    Outcome::Stay
                }
                KeyCode::Down => {
                    *scroll = (*scroll + 1).min(keys.len().saturating_sub(1));
                    Outcome::Stay
                }
                KeyCode::PageUp => {
                    *scroll = scroll.saturating_sub(10);
                    Outcome::Stay
                }
                KeyCode::PageDown => {
                    *scroll = (*scroll + 10).min(keys.len().saturating_sub(1));
                    Outcome::Stay
                }
                _ => Outcome::Stay,
            },
        }
    }

    /// The panel's lines for this width and at most `rows` rows, and where the cursor goes
    /// (row, column) when the panel takes text.
    pub fn lines(&self, theme: &Theme, width: usize, rows: usize) -> (Vec<Line<'static>>, Option<(usize, usize)>) {
        match self {
            Overlay::Command { line, commands, selected, client } => {
                let typed = line.text();
                let mut body = vec![vec![span("› ", theme.accent()), span(typed.to_owned(), Style::default())]];
                let cursor_column = 3 + 2 + text::width(&typed[..line.cursor()]);
                let shown = named(commands, typed);
                let chosen = (*selected).min(shown.len().saturating_sub(1));
                let name_width = shown.iter().map(|&i| text::width(&commands[i].name)).max().unwrap_or(0) + 1;
                let window = window(shown.len(), chosen, rows.saturating_sub(3));
                for (at, &i) in shown.iter().enumerate().take(window.1).skip(window.0) {
                    let command = &commands[i];
                    let current = at == chosen;
                    let mut about = command.about.clone();
                    if let Some(takes) = &command.takes {
                        about = format!("{about} · then type {takes}");
                    }
                    body.push(vec![
                        span(if current { "● " } else { "○ " }, if current { theme.good() } else { theme.muted() }),
                        span(
                            format!("/{:<name_width$}", command.name),
                            if current { theme.accent().add_modifier(ratatui::style::Modifier::BOLD) } else { theme.accent() },
                        ),
                        span(format!(" {about}"), theme.muted()),
                    ]);
                }
                let hint = format!("enter run · ↑↓ choose · esc gives the text to {client}");
                (panel(theme, "Yougori's commands", body, &hint, width), Some((1, cursor_column)))
            }
            Overlay::Switch(flow) => flow.lines(theme, width, rows),
            Overlay::Palette { filter, items, selected } => {
                let visible = matching(items, filter.text());
                let mut body = Vec::new();
                let typed = filter.text();
                let field = if typed.is_empty() {
                    vec![span("› ", theme.accent()), span("Type to filter", theme.muted())]
                } else {
                    vec![span("› ", theme.accent()), span(typed.to_owned(), Style::default())]
                };
                body.push(field);
                let cursor_column = 3 + 2 + text::width(&typed[..filter.cursor()]);
                let label_width = items.iter().map(|i| text::width(&i.label)).max().unwrap_or(0).min(28);
                let rows_left = rows.saturating_sub(3);
                let at = visible.iter().position(|i| i == selected).unwrap_or(0);
                let window = window(visible.len(), at, rows_left);
                for &i in &visible[window.0..window.1] {
                    let item = &items[i];
                    body.push(choice_line(theme, &item.label, &item.hint, i == *selected, item.enabled, label_width));
                }
                if visible.is_empty() {
                    body.push(vec![span("Nothing matches.", theme.muted())]);
                } else if window.1 - window.0 < visible.len() {
                    body.push(vec![span(format!("{} of {}", at + 1, visible.len()), theme.muted())]);
                }
                let lines = panel(theme, "Commands", body, "↑↓ move · enter run · esc close", width);
                (lines, Some((1, cursor_column)))
            }
            Overlay::Picker { title, choices, selected, .. } => {
                let mut body = Vec::new();
                if choices.is_empty() {
                    body.push(vec![span("Nothing to choose from.", theme.muted())]);
                }
                let label_width = choices.iter().map(|c| text::width(&c.label)).max().unwrap_or(0).min(40);
                let window = window(choices.len(), *selected, rows.saturating_sub(2));
                for (i, choice) in choices.iter().enumerate().take(window.1).skip(window.0) {
                    let hint = if choice.current {
                        if choice.hint.is_empty() {
                            "current".to_owned()
                        } else {
                            format!("current · {}", choice.hint)
                        }
                    } else {
                        choice.hint.clone()
                    };
                    body.push(choice_line(theme, &choice.label, &hint, i == *selected, true, label_width));
                }
                if window.1 - window.0 < choices.len() {
                    body.push(vec![span(format!("{} of {}", selected + 1, choices.len()), theme.muted())]);
                }
                (panel(theme, title, body, "↑↓ move · enter choose · esc close", width), None)
            }
            Overlay::Confirm { title, detail, yes, .. } => {
                let room = width.saturating_sub(3);
                let body: Vec<Vec<Span<'static>>> =
                    wrap_text(detail, theme.muted(), room, &[], &[]).into_iter().map(|l| l.spans).collect();
                (panel(theme, title, body, &format!("enter {yes} · esc cancel"), width), None)
            }
            Overlay::Keys { keys, scroll } => {
                let key_width = keys.iter().map(|(k, _)| text::width(k)).max().unwrap_or(0) + 3;
                let room = rows.saturating_sub(2).max(1);
                let start = (*scroll).min(keys.len().saturating_sub(room));
                let mut body: Vec<Vec<Span<'static>>> = keys
                    .iter()
                    .skip(start)
                    .take(room)
                    .map(|(k, what)| {
                        vec![span(format!("{k:<key_width$}"), theme.accent()), span(what.clone(), theme.muted())]
                    })
                    .collect();
                if keys.len() > room {
                    body.pop();
                    body.push(vec![span(format!("↑↓ more ({} of {})", start + 1, keys.len()), theme.muted())]);
                }
                (panel(theme, "Keys", body, "esc close", width), None)
            }
        }
    }
}

fn matching(items: &[Item], filter: &str) -> Vec<usize> {
    let filter = filter.trim().to_lowercase();
    (0..items.len())
        .filter(|&i| {
            filter.is_empty()
                || items[i].label.to_lowercase().contains(&filter)
                || items[i].hint.to_lowercase().contains(&filter)
        })
        .collect()
}

fn reselect(selected: &mut usize, items: &[Item], filter: &str) {
    let visible = matching(items, filter);
    if !visible.contains(selected) {
        *selected = visible.iter().copied().find(|i| items[*i].enabled).or(visible.first().copied()).unwrap_or(0);
    }
}

fn step(selected: &mut usize, visible: &[usize], items: &[Item], by: isize) {
    if visible.is_empty() {
        return;
    }
    let at = visible.iter().position(|i| i == selected).unwrap_or(0) as isize;
    let count = visible.len() as isize;
    for n in 1..=count {
        let next = visible[((at + by * n).rem_euclid(count)) as usize];
        if items[next].enabled {
            *selected = next;
            return;
        }
    }
}

/// The rows of a list to show so `at` stays visible.
pub fn window(count: usize, at: usize, rows: usize) -> (usize, usize) {
    let rows = rows.max(1);
    if count <= rows {
        return (0, count);
    }
    let rows = rows.saturating_sub(1).max(1);
    let start = (at + 1).saturating_sub(rows).min(count - rows);
    (start, start + rows)
}

/// One choice as the CLI draws it: ● selected in green, ○ the others.
pub fn choice_line(
    theme: &Theme,
    label: &str,
    hint: &str,
    selected: bool,
    enabled: bool,
    label_width: usize,
) -> Vec<Span<'static>> {
    let mark = if selected { span("● ", theme.good()) } else { span("○ ", theme.muted()) };
    let label_style = if !enabled {
        theme.muted()
    } else if selected {
        theme.bold()
    } else {
        Style::default()
    };
    let label = truncate(label, label_width.max(1));
    let padded = format!("{label:<label_width$}");
    let mut spans = vec![mark, span(padded, label_style)];
    if !hint.is_empty() {
        spans.push(pad(2));
        spans.push(span(hint.to_owned(), theme.muted()));
    }
    spans
}

/// A panel in the CLI's style: ◆ and the title, the body behind │ rails, └ and the key hints.
pub fn panel(
    theme: &Theme,
    title: &str,
    body: Vec<Vec<Span<'static>>>,
    hint: &str,
    width: usize,
) -> Vec<Line<'static>> {
    let room = width.saturating_sub(3).max(1);
    let mut lines = vec![Line::from(truncate_spans(
        vec![span("◆  ", theme.accent()), span(title.to_owned(), theme.bold())],
        width,
    ))];
    for spans in body {
        let mut row = vec![span("│  ", theme.accent())];
        row.extend(truncate_spans(spans, room));
        lines.push(Line::from(row));
    }
    lines.push(Line::from(truncate_spans(
        vec![span("└  ", theme.accent()), span(hint.to_owned(), theme.muted())],
        width,
    )));
    lines
}

#[cfg(test)]
mod tests {
    use super::*;

    fn key(code: KeyCode) -> KeyEvent {
        KeyEvent::new(code, KeyModifiers::NONE)
    }

    fn items() -> Vec<Item> {
        vec![
            Item { action: Action::Model, label: "Switch model".into(), hint: "m".into(), enabled: true },
            Item {
                action: Action::Slash("new".into()),
                label: "New session".into(),
                hint: "/new".into(),
                enabled: false,
            },
            Item { action: Action::Quit, label: "Quit".into(), hint: String::new(), enabled: true },
        ]
    }

    #[test]
    fn the_palette_filters_and_skips_what_is_not_offered() {
        let mut palette = Overlay::palette(items());
        assert_eq!(palette.key(key(KeyCode::Down)), Outcome::Stay);
        assert_eq!(palette.key(key(KeyCode::Enter)), Outcome::Run(Action::Quit), "the disabled entry is skipped");
        let mut palette = Overlay::palette(items());
        for c in "mod".chars() {
            palette.key(key(KeyCode::Char(c)));
        }
        assert_eq!(palette.key(key(KeyCode::Enter)), Outcome::Run(Action::Model));
        assert_eq!(palette.key(key(KeyCode::Esc)), Outcome::Close);
    }

    #[test]
    fn pickers_choose_by_arrow_or_number() {
        let choices = vec![
            Choice { id: "a".into(), label: "a".into(), hint: String::new(), current: true },
            Choice { id: "b".into(), label: "b".into(), hint: String::new(), current: false },
        ];
        let mut picker = Overlay::picker(Which::Models, "Switch model", choices);
        picker.key(key(KeyCode::Down));
        assert_eq!(picker.key(key(KeyCode::Enter)), Outcome::Pick(Which::Models, "b".into()));
        assert_eq!(picker.key(key(KeyCode::Char('1'))), Outcome::Pick(Which::Models, "a".into()));
    }

    #[test]
    fn a_command_line_keeps_only_what_names_a_command() {
        let commands = vec![
            Command { name: "model".into(), about: "switch model".into(), takes: Some("id".into()) },
            Command { name: "new".into(), about: "start again".into(), takes: None },
        ];
        assert!(names_command(&commands, "/"));
        assert!(names_command(&commands, "/mo"));
        assert!(names_command(&commands, "/MODEL gemma"));
        assert!(!names_command(&commands, "/new thing"), "new takes nothing after it");
        assert!(!names_command(&commands, "/news"));
        assert!(!names_command(&commands, "//"));
        assert!(!names_command(&commands, "model"));
        let mut line = Overlay::command(commands.clone(), "Shannon");
        assert_eq!(line.key(key(KeyCode::Char('n'))), Outcome::Stay);
        assert_eq!(line.key(key(KeyCode::Enter)), Outcome::Command("new".into(), String::new()));
        let mut line = Overlay::command(commands.clone(), "Shannon");
        assert_eq!(line.key(key(KeyCode::Char('s'))), Outcome::Pass("/s".into()));
        let mut line = Overlay::command(commands, "Shannon");
        assert_eq!(line.key(key(KeyCode::Backspace)), Outcome::Close);
    }

    #[test]
    fn windows_keep_the_selection_visible() {
        assert_eq!(window(3, 0, 5), (0, 3));
        assert_eq!(window(20, 0, 5), (0, 4));
        assert_eq!(window(20, 10, 5), (7, 11));
        assert_eq!(window(20, 19, 5), (16, 20));
    }
}
