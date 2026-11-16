//! A harness's own terminal client (`Protocol::Terminal`) shown in a pane of the Yougori screen:
//! the header line (Yougori, the harness, the model), the client's screen, and the status line
//! (what the VM reaches, the keys). The client runs in a pseudo-terminal sized to the pane; its
//! output is parsed into a screen (vt100) and drawn with its colours; every key and paste goes to
//! it except Ctrl+P, which opens the palette (switch harness or model, send Ctrl+P, keys, quit),
//! and a `/` at the start of a line, which opens Yougori's commands (/harness, /model, /new,
//! /help) as in the chat; what names none of them goes on to the client as typed.
use super::draw::{Frame, ScrollRegion};
use super::mask::Masker;
use super::overlay::{Action, Choice, Command, Confirmable, Item, Outcome, Overlay, Which};
use super::switcher::{Flow, FlowOutcome, Mode as Where, Pending};
use super::text::{self, pad, span, truncate_spans, wrap_text};
use super::theme::{self, clock, Depth, Rgb, Theme, BLUE, GRAY, GREEN, SKY};
use crate::history::{self, Chat, Listing};
use crate::spec::EngineSpec;
use crossterm::event::{KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
use portable_pty::{CommandBuilder, PtySize};
use ratatui::buffer::Buffer;
use ratatui::layout::{Position, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::Line;
use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::num::NonZeroU16;
use std::time::{Duration, Instant};
use tokio::sync::mpsc::UnboundedSender;

/// Rows of the client's output kept for scrolling back.
const SCROLLBACK: usize = 5000;

/// Each harness's own pair of colours, from the first to its signature, as its terminal draws with
/// them (terminal_ui.py BRANDS): its name in the header. The security harnesses and the harnesses
/// of Yougori's terminal (agent_kit.py) have one; the others keep the plain bold name.
pub fn brand(harness: &str) -> Option<(Rgb, Rgb)> {
    let hex = |c: u32| Rgb((c >> 16) as u8, (c >> 8) as u8, c as u8);
    let pair = match harness {
        "hexstrike" => (0xff6b6b, 0xffa94d),
        "pentagi" => (0xf783ac, 0xb197fc),
        "shannon" => (0x74c0fc, 0x38d9a9),
        "strix" => (0xff922b, 0xffd43b),
        "pentestgpt" => (0x69db7c, 0x3bc9db),
        "langgraph" => (0x4dabf7, 0x9775fa),
        "deepagents" => (0x748ffc, 0x63e6be),
        "crewai" => (0xff8787, 0xffc078),
        "smolagents" => (0xffd43b, 0xff922b),
        "strands" => (0x66d9e8, 0x4dabf7),
        "adk" => (0x69db7c, 0x4dabf7),
        "agentframework" => (0x74c0fc, 0xda77f2),
        "vanna" => (0x9775fa, 0xf783ac),
        "gptresearcher" => (0x38d9a9, 0x74c0fc),
        "opendeepresearch" => (0xb197fc, 0x66d9e8),
        "paperqa" => (0xffa94d, 0xf783ac),
        "eigent" => (0xda77f2, 0x748ffc),
        "browseruse" => (0xffa8a8, 0xffd8a8),
        "pipecat" => (0x63e6be, 0xa9e34b),
        "finrobot" => (0x8ce99a, 0x66d9e8),
        "pandasai" => (0xffc078, 0xff8787),
        "taskweaver" => (0x74c0fc, 0x8ce99a),
        "tradingagents" => (0xffd43b, 0x69db7c),
        "chemcrow" => (0x63e6be, 0xda77f2),
        "deerflow" => (0xffa94d, 0x74c0fc),
        "biomni" => (0x8ce99a, 0x38d9a9),
        "openmanus" => (0xf783ac, 0xffd43b),
        "livekit" => (0xa9e34b, 0x66d9e8),
        "agenttars" => (0xff8787, 0x748ffc),
        "stagehand" => (0xffd8a8, 0x63e6be),
        "geoagent" => (0x69db7c, 0xffd43b),
        _ => return None,
    };
    Some((hex(pair.0), hex(pair.1)))
}

/// What the client's threads report, tagged with the child they belong to.
pub enum Message {
    Output(u64, Vec<u8>),
    Exited(u64, Result<portable_pty::ExitStatus, String>),
}

/// Answers the client's questions to its terminal (cursor position, device attributes, size,
/// colours), which the pane's screen cannot answer itself, and keeps what the screen kit of
/// Yougori's harness clients says of its input line and of the chat.
#[derive(Default)]
pub struct Replies {
    pub out: Vec<u8>,
    /// The client's latest word on the line the person types (OSC 7717 `line;empty` or
    /// `line;text`): Some(true) when it is empty. Only a hint for where `/` starts a command:
    /// whatever the client prints, a command runs only when the person types it.
    pub line: Option<bool>,
    /// The chat as Yougori's own clients tell it (OSC 7718 `turn;user|assistant;BASE64` and
    /// `tool;BASE64`), in order, for the history.
    pub said: Vec<Said>,
}

/// One thing a client said of the chat (OSC 7718).
#[derive(Clone, Debug, PartialEq)]
pub enum Said {
    User(String),
    Assistant(String),
    Tool(String),
}

/// Standard base64 (with or without padding), as the screen kit writes it; None when it is not.
pub fn base64_text(data: &[u8]) -> Option<String> {
    let value = |c: u8| -> Option<u32> {
        Some(match c {
            b'A'..=b'Z' => c - b'A',
            b'a'..=b'z' => c - b'a' + 26,
            b'0'..=b'9' => c - b'0' + 52,
            b'+' => 62,
            b'/' => 63,
            _ => return None,
        } as u32)
    };
    let data: Vec<u8> = data.iter().copied().filter(|c| *c != b'=').collect();
    if data.len() > 256 * 1024 || data.len() % 4 == 1 {
        return None;
    }
    let mut out = Vec::with_capacity(data.len() * 3 / 4);
    for chunk in data.chunks(4) {
        let mut acc = 0u32;
        for (i, c) in chunk.iter().enumerate() {
            acc |= value(*c)? << (18 - 6 * i);
        }
        let bytes = [(acc >> 16) as u8, (acc >> 8) as u8, acc as u8];
        out.extend_from_slice(&bytes[..chunk.len() - 1]);
    }
    String::from_utf8(out).ok()
}

impl vt100::Callbacks for Replies {
    fn unhandled_csi(
        &mut self,
        screen: &mut vt100::Screen,
        i1: Option<u8>,
        i2: Option<u8>,
        params: &[&[u16]],
        c: char,
    ) {
        let first = params.first().and_then(|p| p.first()).copied().unwrap_or(0);
        let reply = match (i1, i2, c) {
            (None, None, 'n') if first == 6 => {
                let (row, col) = screen.cursor_position();
                format!("\x1b[{};{}R", row + 1, col + 1)
            }
            (Some(b'?'), None, 'n') if first == 6 => {
                let (row, col) = screen.cursor_position();
                format!("\x1b[?{};{}R", row + 1, col + 1)
            }
            (None, None, 'n') if first == 5 => "\x1b[0n".into(),
            (None, None, 'c') if first == 0 => "\x1b[?62;22c".into(),
            (Some(b'>'), None, 'c') if first == 0 => "\x1b[>1;10;0c".into(),
            (None, None, 't') if first == 18 => {
                let (rows, cols) = screen.size();
                format!("\x1b[8;{rows};{cols}t")
            }
            (Some(b'?'), Some(b'$'), 'p') => format!("\x1b[?{first};0$y"),
            (None, Some(b'$'), 'p') => format!("\x1b[{first};0$y"),
            _ => return,
        };
        self.out.extend_from_slice(reply.as_bytes());
    }

    fn unhandled_osc(&mut self, _: &mut vt100::Screen, params: &[&[u8]]) {
        // Foreground and background colour queries: a light text on a dark terminal.
        match params {
            [b"10", b"?"] => self.out.extend_from_slice(b"\x1b]10;rgb:e4e4/e4e4/e7e7\x1b\\"),
            [b"11", b"?"] => self.out.extend_from_slice(b"\x1b]11;rgb:0000/0000/0000\x1b\\"),
            [b"7717", b"line", b"empty"] => self.line = Some(true),
            [b"7717", b"line", b"text"] => self.line = Some(false),
            [b"7718", b"turn", role, data] if self.said.len() < 256 => {
                if let Some(text) = base64_text(data) {
                    match *role {
                        b"user" => self.said.push(Said::User(text)),
                        b"assistant" => self.said.push(Said::Assistant(text)),
                        _ => {}
                    }
                }
            }
            [b"7718", b"tool", data] if self.said.len() < 256 => {
                if let Some(text) = base64_text(data) {
                    self.said.push(Said::Tool(text));
                }
            }
            _ => {}
        }
    }
}

/// A running client.
pub struct Child {
    pub generation: u64,
    master: Box<dyn portable_pty::MasterPty + Send>,
    input: std::sync::mpsc::Sender<Vec<u8>>,
    killer: Box<dyn portable_pty::ChildKiller + Send + Sync>,
    /// The spec's services, stopped with the client.
    _services: Option<crate::engines::services::Services>,
}

impl Child {
    pub fn send(&self, bytes: Vec<u8>) {
        if !bytes.is_empty() {
            let _ = self.input.send(bytes);
        }
    }

    pub fn resize(&self, rows: u16, cols: u16) {
        let _ = self.master.resize(PtySize { rows, cols, pixel_width: 0, pixel_height: 0 });
    }

    /// The erase character of the client's terminal while it reads whole lines (canonical mode,
    /// as a plain `input()` or a shell without line editing reads): None while it reads keys
    /// itself, or where the mode cannot be read.
    pub fn line_erase(&self) -> Option<u8> {
        #[cfg(unix)]
        {
            let fd = self.master.as_raw_fd()?;
            // SAFETY: tcgetattr writes the termios of the pty's slave side (Linux answers for the
            // slave on its master) into a zeroed struct it owns for the call.
            let mut termios: libc::termios = unsafe { std::mem::zeroed() };
            if unsafe { libc::tcgetattr(fd, &mut termios) } != 0 || termios.c_lflag & libc::ICANON == 0 {
                return None;
            }
            Some(termios.c_cc[libc::VERASE])
        }
        #[cfg(not(unix))]
        None
    }

    pub fn kill(&mut self) {
        let _ = self.killer.kill();
    }
}

impl Drop for Child {
    fn drop(&mut self) {
        let _ = self.killer.kill();
    }
}

/// The client's whole environment: the spec's (secrets filled in) and what a terminal client
/// needs to know about what it draws on. The pane understands xterm's sequences and adapts any
/// colour to the person's terminal; when that terminal shows no colour (NO_COLOR) the client is
/// told so, and not that it has 24-bit colour, so it marks what is selected with other cues
/// instead of colours the pane would drop. YOUGORI_PANE tells Yougori's own screen kit that it
/// draws in this pane, so it says when its input line is empty (OSC 7717).
pub fn client_env(spec: &EngineSpec, depth: Depth) -> BTreeMap<String, String> {
    let mut environment = spec.environment();
    environment.entry("TERM".into()).or_insert_with(|| "xterm-256color".into());
    environment.insert("YOUGORI_PANE".into(), "1".into());
    if depth == Depth::None {
        environment.remove("COLORTERM");
        environment.insert("NO_COLOR".into(), "1".into());
    } else {
        environment.entry("COLORTERM".into()).or_insert_with(|| "truecolor".into());
    }
    environment
}

/// Starts the spec's client in a pseudo-terminal of this size, for a person's terminal of this
/// colour depth. Its output and exit arrive on `tx`.
pub fn spawn(
    spec: &EngineSpec,
    rows: u16,
    cols: u16,
    generation: u64,
    services: Option<crate::engines::services::Services>,
    depth: Depth,
    tx: UnboundedSender<Message>,
) -> anyhow::Result<Child> {
    let program =
        spec.argv.first().ok_or_else(|| anyhow::anyhow!("The engine spec has no command for {}.", spec.name))?;
    let pair = portable_pty::native_pty_system()
        .openpty(PtySize { rows: rows.max(2), cols: cols.max(10), pixel_width: 0, pixel_height: 0 })
        .map_err(|e| anyhow::anyhow!("Could not open a terminal for {}: {e}", spec.name))?;
    let mut command = CommandBuilder::new(program);
    command.args(&spec.argv[1..]);
    command.env_clear();
    for (name, value) in &client_env(spec, depth) {
        command.env(name, value);
    }
    command.cwd(&spec.cwd);
    let mut child = pair
        .slave
        .spawn_command(command)
        .map_err(|e| anyhow::anyhow!("Could not start {} ({program}): {e}", spec.name))?;
    drop(pair.slave);
    let killer = child.clone_killer();
    let mut reader =
        pair.master.try_clone_reader().map_err(|e| anyhow::anyhow!("Could not read {}'s terminal: {e}", spec.name))?;
    let mut writer =
        pair.master.take_writer().map_err(|e| anyhow::anyhow!("Could not write to {}'s terminal: {e}", spec.name))?;
    let output = tx.clone();
    std::thread::Builder::new().name("yougori-pane-read".into()).spawn(move || {
        let mut buffer = vec![0u8; 32 * 1024];
        loop {
            match reader.read(&mut buffer) {
                Ok(0) | Err(_) => break,
                Ok(n) => {
                    if output.send(Message::Output(generation, buffer[..n].to_vec())).is_err() {
                        break;
                    }
                }
            }
        }
    })?;
    let (input, keys) = std::sync::mpsc::channel::<Vec<u8>>();
    std::thread::Builder::new().name("yougori-pane-write".into()).spawn(move || {
        for bytes in keys {
            if writer.write_all(&bytes).and_then(|_| writer.flush()).is_err() {
                break;
            }
        }
    })?;
    std::thread::Builder::new().name("yougori-pane-wait".into()).spawn(move || {
        let status = child.wait().map_err(|e| e.to_string());
        let _ = tx.send(Message::Exited(generation, status));
    })?;
    Ok(Child { generation, master: pair.master, input, killer, _services: services })
}

/// The bytes a key sends to a terminal program.
pub fn key_bytes(key: KeyEvent, application_cursor: bool) -> Vec<u8> {
    let control = key.modifiers.contains(KeyModifiers::CONTROL);
    let alt = key.modifiers.contains(KeyModifiers::ALT);
    let shift = key.modifiers.contains(KeyModifiers::SHIFT);
    let modifier = 1 + u8::from(shift) + 2 * u8::from(alt) + 4 * u8::from(control);
    let cursor = |letter: char| -> Vec<u8> {
        if modifier > 1 {
            format!("\x1b[1;{modifier}{letter}")
        } else if application_cursor {
            format!("\x1bO{letter}")
        } else {
            format!("\x1b[{letter}")
        }
        .into_bytes()
    };
    let tilde = |n: u8| -> Vec<u8> {
        if modifier > 1 { format!("\x1b[{n};{modifier}~") } else { format!("\x1b[{n}~") }.into_bytes()
    };
    let function = |letter: char| -> Vec<u8> {
        if modifier > 1 { format!("\x1b[1;{modifier}{letter}") } else { format!("\x1bO{letter}") }.into_bytes()
    };
    let (mut bytes, carries_modifier) = match key.code {
        KeyCode::Char(c) if control => {
            let byte = match c.to_ascii_lowercase() {
                c @ 'a'..='z' => Some(c as u8 & 0x1f),
                '@' | ' ' | '2' => Some(0),
                '[' | '3' => Some(0x1b),
                '\\' | '4' => Some(0x1c),
                ']' | '5' => Some(0x1d),
                '^' | '6' => Some(0x1e),
                '_' | '-' | '7' => Some(0x1f),
                '?' | '8' => Some(0x7f),
                _ => None,
            };
            (byte.map(|b| vec![b]).unwrap_or_else(|| c.to_string().into_bytes()), false)
        }
        KeyCode::Char(c) => (c.to_string().into_bytes(), false),
        // Shift+Enter is a new line in the clients that have one: sent as Alt+Enter.
        KeyCode::Enter if shift => (b"\x1b\r".to_vec(), true),
        KeyCode::Enter => (vec![b'\r'], false),
        KeyCode::Backspace if control => (vec![0x08], false),
        KeyCode::Backspace => (vec![0x7f], false),
        KeyCode::Tab => (vec![b'\t'], false),
        KeyCode::BackTab => (b"\x1b[Z".to_vec(), true),
        KeyCode::Esc => (vec![0x1b], false),
        KeyCode::Up => (cursor('A'), true),
        KeyCode::Down => (cursor('B'), true),
        KeyCode::Right => (cursor('C'), true),
        KeyCode::Left => (cursor('D'), true),
        KeyCode::Home => (cursor('H'), true),
        KeyCode::End => (cursor('F'), true),
        KeyCode::Insert => (tilde(2), true),
        KeyCode::Delete => (tilde(3), true),
        KeyCode::PageUp => (tilde(5), true),
        KeyCode::PageDown => (tilde(6), true),
        KeyCode::F(n @ 1..=4) => (function(['P', 'Q', 'R', 'S'][usize::from(n) - 1]), true),
        KeyCode::F(n @ 5..=12) => (tilde([15, 17, 18, 19, 20, 21, 23, 24][usize::from(n) - 5]), true),
        _ => (Vec::new(), false),
    };
    if alt && !carries_modifier && !bytes.is_empty() {
        bytes.insert(0, 0x1b);
    }
    bytes
}

/// What a key sends to a client whose terminal reads whole lines (`erase`: its erase character).
/// The line discipline edits that line, and knows only its erase character, so Backspace and
/// Ctrl+H both send it (the Windows console gives Backspace as Ctrl+H, and its launcher passes keys
/// on as the console gives them). Keys sent as escape sequences (the arrows, Delete, Home, End, the
/// function keys, Alt with a key) would only be echoed into the line as codes such as ^[[3~, so
/// they send nothing; Shift+Enter sends the line.
pub fn line_bytes(key: KeyEvent, bytes: Vec<u8>, erase: u8) -> Vec<u8> {
    let control = key.modifiers.contains(KeyModifiers::CONTROL);
    match key.code {
        KeyCode::Backspace => return if erase == 0 { bytes } else { vec![erase] },
        KeyCode::Char('h' | 'H') if control && erase != 0 => return vec![erase],
        KeyCode::Enter => return vec![b'\r'],
        _ => {}
    }
    if bytes.first() == Some(&0x1b) {
        return Vec::new();
    }
    bytes
}

/// A paste as the client takes it: wrapped in bracketed-paste marks when it asked for them.
pub fn paste_bytes(text: &str, bracketed: bool) -> Vec<u8> {
    let text = text.replace("\r\n", "\r").replace('\n', "\r");
    let text: String = text.chars().filter(|c| !c.is_control() || matches!(c, '\r' | '\t')).collect();
    if bracketed {
        format!("\x1b[200~{text}\x1b[201~").into_bytes()
    } else {
        text.into_bytes()
    }
}

#[derive(Clone, Debug, PartialEq)]
pub enum PanePhase {
    Starting { since: Instant },
    Running { since: Instant },
    Switching { model: String, since: Instant, step: String },
    /// Switching harness (/harness): `step` is the switch's latest progress.
    Changing { name: String, since: Instant, step: String },
    Ended { message: String, failed: bool },
    Quitting { since: Instant },
}

#[derive(Clone, Debug, PartialEq)]
pub enum PaneEffect {
    Start,
    Restart(String),
    /// /new: the client stops, and once it has ended (or after RENEW_WAIT) starts fresh.
    Renew,
    /// Switch harness (/harness): in place the client stops first, then the switch command runs.
    Switch(Box<Pending>),
    /// Esc while switching harness: the switch command is asked to stop.
    CancelSwitch,
    Redraw,
    Quit,
    /// Save the chat on the person's computer (history.rs).
    Save(Box<Chat>),
    /// /history: list the chats kept.
    History,
    /// Open this chat from /history.
    Open(String),
    /// Esc after a sign-in that did not finish: back to the model before it.
    Back,
}

/// The variable a client that takes an earlier chat reads (Yougori's own: agent_kit.py): the file
/// that holds it, written before the client starts. The spec says a client takes one with
/// YOUGORI_TAKES_CONTEXT.
pub const CONTEXT_FILE: &str = "YOUGORI_CONTEXT_FILE";
pub const TAKES_CONTEXT: &str = "YOUGORI_TAKES_CONTEXT";

/// An earlier chat a client takes over when it starts: written to a file for it, compacted to fit
/// (the model's notes on its oldest part when it does not fit whole).
pub struct Takeover {
    pub chat: Chat,
    pub budget: usize,
    /// The relay, its key, the model that writes the notes and how long they may be.
    pub notes: Option<(String, String, String, u32)>,
    /// How much of the earlier chat the notes request may carry (history::notes_room).
    pub room: usize,
    pub file: std::path::PathBuf,
}

impl Takeover {
    /// Writes the chat for the client; the file's path, or None when there is nothing to give or
    /// it could not be written.
    pub async fn write(self) -> Option<String> {
        let turns = &self.chat.turns;
        let mut notes = None;
        if history::needs_notes(&self.chat, turns, self.budget) {
            if let Some((base, key, model, most)) = &self.notes {
                let older = history::newest(turns, self.budget / 2);
                let text = history::transcript(&turns[..older], &self.chat.screen, self.room);
                notes = history::summarize(base, key, model, &text, *most).await.ok();
            }
        }
        let context = history::context(&self.chat, turns, self.budget, notes.as_deref())?;
        let file = self.file;
        if let Some(parent) = file.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let written = {
            #[cfg(unix)]
            {
                use std::io::Write as _;
                use std::os::unix::fs::OpenOptionsExt;
                std::fs::OpenOptions::new()
                    .write(true)
                    .create(true)
                    .truncate(true)
                    .mode(0o600)
                    .open(&file)
                    .and_then(|mut out| out.write_all(context.as_bytes()))
            }
            #[cfg(not(unix))]
            {
                std::fs::write(&file, context.as_bytes())
            }
        };
        written.ok().map(|_| file.to_string_lossy().into_owned())
    }
}

pub struct Pane {
    pub spec: EngineSpec,
    pub theme: Theme,
    pub masker: Masker,
    pub epoch: Instant,
    pub phase: PanePhase,
    /// A short message for the status line (a model switch that was undone), and since when.
    pub note: Option<(String, Instant)>,
    pub parser: vt100::Parser<Replies>,
    pub child: Option<Child>,
    pub generation: u64,
    pub overlay: Option<Overlay>,
    /// Rows scrolled back into the client's earlier output (0: its live screen).
    pub scrollback: usize,
    pub failed: bool,
    pub effects: Vec<PaneEffect>,
    pub dirty: bool,
    pub models: Vec<String>,
    /// While a model switch is prepared: what the pane goes back to if the new model cannot be
    /// prepared (how it was, or the client's end when the client ended meanwhile).
    pub switch_from: Option<PanePhase>,
    /// The harness switch under way, and what the pane goes back to when a new VM's switch fails.
    pub change: Option<Box<Pending>>,
    pub change_from: Option<PanePhase>,
    /// What the screen before this one handed on (its conversation), for the screen after it.
    pub kept: super::Carry,
    /// How many characters the line the person types to the client holds, as far as the pane
    /// knows (from the keys it sent, and the screen kit's word): None after keys that move in the
    /// line. A `/` opens Yougori's commands unless the line is known to hold something.
    pub typed: Option<usize>,
    /// The chat as it goes (history.rs): the turns Yougori's own clients tell, or what the
    /// client's screen showed once the person typed something to it.
    pub chat: Chat,
    /// Whether the client running has the chat (it took it over when it started).
    pub given: bool,
    /// The person sent the client a line since it started.
    pub spoke: bool,
    /// Whether the person's computer keeps the chats (None until a save or a listing says).
    pub stored: Option<bool>,
    /// The pane runs a client's sign-in to a plan (ui::SignIn): no chat of its own, no commands of
    /// Yougori's, and Esc goes back to the model before (`back`: its name, when there is one).
    pub signing: bool,
    pub back: Option<String>,
}

/// Yougori's commands in a terminal pane (name, what it does, what to type after it): the chat's
/// own, /new and /history. While the client runs, /exit and /quit are its own (each harness's
/// /exit ends it its way); once it has ended they quit, as `q` does.
const PANE_COMMANDS: [(&str, &str, Option<&str>); 7] = [
    ("harness", "switch to another harness", Some("name")),
    ("model", "switch model", Some("id")),
    ("new", "start {name} again, fresh (this chat is kept)", None),
    ("history", "open an earlier chat", None),
    ("help", "commands and keys", None),
    ("exit", "quit Yougori", None),
    ("quit", "quit Yougori", None),
];

fn parser(rows: u16, cols: u16) -> vt100::Parser<Replies> {
    let rows = NonZeroU16::new(rows.max(1)).unwrap_or(NonZeroU16::MIN);
    let cols = NonZeroU16::new(cols.max(1)).unwrap_or(NonZeroU16::MIN);
    vt100::Parser::new_with_callbacks(rows, cols, SCROLLBACK, Replies::default())
}

/// The pane's rows and columns inside a screen of this size.
pub fn pane_size(width: u16, height: u16) -> (u16, u16) {
    (height.saturating_sub(2).max(1), width.max(1))
}

impl Pane {
    pub fn new(spec: EngineSpec, theme: Theme, masker: Masker, width: u16, height: u16, now: Instant) -> Pane {
        let (rows, cols) = pane_size(width, height);
        let models = if spec.models.is_empty() { vec![spec.model.clone()] } else { spec.models.clone() };
        let chat = Chat::new(&spec.harness, &spec.name, &spec.model);
        Pane {
            chat,
            given: false,
            spoke: false,
            stored: None,
            signing: false,
            back: None,
            spec,
            theme,
            masker,
            epoch: now,
            phase: PanePhase::Starting { since: now },
            note: None,
            parser: parser(rows, cols),
            child: None,
            generation: 0,
            overlay: None,
            scrollback: 0,
            failed: false,
            effects: Vec::new(),
            dirty: true,
            models,
            switch_from: None,
            change: None,
            change_from: None,
            kept: super::Carry::default(),
            typed: Some(0),
        }
    }

    pub fn running(&self) -> bool {
        matches!(self.phase, PanePhase::Running { .. }) && self.child.is_some()
    }

    pub fn animating(&self) -> bool {
        matches!(
            self.phase,
            PanePhase::Starting { .. } | PanePhase::Switching { .. } | PanePhase::Changing { .. } | PanePhase::Quitting { .. }
        )
    }

    /// The note while it still shows.
    fn note_shown(&self, now: Instant) -> Option<&str> {
        self.note.as_ref().filter(|(_, at)| now.saturating_duration_since(*at) < NOTE_TIME).map(|(n, _)| n.as_str())
    }

    /// Whether the status line changes by itself (the note going away).
    pub fn note_pending(&self, now: Instant) -> bool {
        self.note
            .as_ref()
            .is_some_and(|(_, at)| now.saturating_duration_since(*at) <= NOTE_TIME + Duration::from_millis(200))
    }

    pub fn exit_code(&self) -> i32 {
        i32::from(self.failed)
    }

    /// A new client started: a fresh screen for it.
    pub fn started(&mut self, child: Child, now: Instant) {
        let (rows, cols) = self.parser.screen().size();
        self.parser = parser(rows.get(), cols.get());
        // The screen may have changed size while it started.
        child.resize(rows.get(), cols.get());
        self.generation = child.generation;
        self.child = Some(child);
        self.phase = PanePhase::Running { since: now };
        self.switch_from = None;
        self.scrollback = 0;
        self.failed = false;
        self.typed = Some(0);
        // What the client before showed is in the chat already (saved before it stopped).
        self.spoke = false;
        self.dirty = true;
    }

    /// /new began: the client is asked to stop, and the pane shows the next one starting on a
    /// fresh screen.
    pub fn renewing(&mut self, now: Instant) {
        let (rows, cols) = self.parser.screen().size();
        self.parser = parser(rows.get(), cols.get());
        self.phase = PanePhase::Starting { since: now };
        self.scrollback = 0;
        self.typed = Some(0);
        self.dirty = true;
    }

    /// The client's whole screen as text: the rows scrolled out of view, then the screen.
    pub fn screen_text(&mut self) -> String {
        let screen = self.parser.screen_mut();
        let at = screen.scrollback();
        let total = screen.scrollback_len();
        let (rows, cols) = screen.size();
        let (rows, cols) = (usize::from(rows.get()), cols.get());
        let mut lines: Vec<String> = Vec::new();
        let mut back = total;
        while back > 0 {
            screen.set_scrollback(back);
            let take = back.min(rows);
            lines.extend(screen.rows(0, cols).take(take));
            back -= take;
        }
        screen.set_scrollback(0);
        lines.extend(screen.rows(0, cols));
        screen.set_scrollback(at);
        while lines.last().is_some_and(|line| line.trim().is_empty()) {
            lines.pop();
        }
        lines.iter().map(|line| line.trim_end()).collect::<Vec<_>>().join("\n")
    }

    /// What the client said of the chat (OSC 7718) goes into it; a whole reply saves it.
    fn take_said(&mut self) {
        let said = std::mem::take(&mut self.parser.callbacks_mut().said);
        let mut replied = false;
        for item in said {
            match item {
                Said::User(text) => {
                    self.spoke = true;
                    self.chat.user(&text);
                }
                Said::Assistant(text) => {
                    self.chat.reply(&text, true);
                    replied = true;
                }
                Said::Tool(title) => self.chat.tool(&title),
            }
        }
        if replied {
            self.save_chat();
        }
    }

    /// Saves the chat when something was said in it: with a client that tells no turns, what its
    /// screen shows once the person typed to it. Secrets are masked as the screen masks them.
    pub fn save_chat(&mut self) {
        if self.signing {
            return;
        }
        if self.chat.turns.is_empty() && self.spoke {
            let text = self.screen_text();
            self.chat.set_screen(&text);
        }
        if self.chat.is_empty() {
            return;
        }
        self.chat.version += 1;
        let mut chat = self.chat.clone();
        let mask = |text: &str| self.masker.mask(text).into_owned();
        chat.title = mask(&chat.title);
        chat.screen = mask(&chat.screen);
        for turn in &mut chat.turns {
            turn.text = mask(&turn.text);
            turn.tools = turn.tools.iter().map(|tool| mask(tool)).collect();
        }
        self.effects.push(PaneEffect::Save(Box::new(chat)));
    }

    /// Whether this harness's client takes an earlier chat when it starts (Yougori's own do).
    pub fn takes_context(&self) -> bool {
        self.spec.env.get(TAKES_CONTEXT).is_some_and(|value| value == "1")
    }

    /// The chat for the client about to start, when it has not seen it and takes one: it has it
    /// from then on.
    pub fn takeover(&mut self) -> Option<Takeover> {
        if self.given || self.chat.is_empty() || !self.takes_context() {
            return None;
        }
        self.given = true;
        let notes = self
            .spec
            .models
            .iter()
            .find(|model| **model == self.spec.model && !crate::spec::subscribed(model))
            .or_else(|| self.spec.models.iter().find(|model| !crate::spec::subscribed(model)))
            .map(|model| (self.spec.base_url.clone(), self.spec.api_key.clone(), model.clone(), self.spec.max_output.min(1536)));
        // Within the window of the model that writes the notes.
        let own = notes.as_ref().is_some_and(|(_, _, model, _)| *model == self.spec.model);
        Some(Takeover {
            chat: self.chat.clone(),
            budget: history::budget(self.spec.context_window),
            notes,
            room: history::notes_room(self.spec.context_window, self.spec.max_output, own),
            file: std::path::Path::new(&self.spec.state_dir).join("earlier-chat.md"),
        })
    }

    /// A new chat for the client that starts fresh (/new): the one before is kept.
    pub fn new_chat(&mut self) {
        self.save_chat();
        self.chat = Chat::new(&self.spec.harness, &self.spec.name, &self.spec.model);
        self.given = false;
        self.spoke = false;
    }

    /// A save's outcome: whether the person's computer keeps the chats.
    pub fn saved(&mut self, outcome: Result<bool, String>, now: Instant) {
        match outcome {
            Ok(kept) => self.stored = Some(kept),
            Err(error) => {
                self.note = Some((format!("This chat could not be saved: {}", text::clean(&error).trim()), now));
                self.dirty = true;
            }
        }
    }

    /// /history's list came: its panel opens (or what went wrong shows in the status line).
    pub fn listed(&mut self, listing: Result<Listing, String>, now: Instant) {
        self.dirty = true;
        let listing = match listing {
            Ok(listing) => listing,
            Err(error) => {
                self.note = Some((format!("Could not read the chats: {}", text::clean(&error).trim()), now));
                return;
            }
        };
        self.stored = Some(listing.kept);
        if listing.chats.is_empty() {
            self.note = Some(("No chats yet. Each chat is kept once something is said in it.".into(), now));
            return;
        }
        let at = history::now();
        let choices = listing
            .chats
            .iter()
            .map(|summary| Choice {
                id: summary.id.clone(),
                label: if summary.title.trim().is_empty() { "Untitled chat".into() } else { self.masker.mask(&summary.title).into_owned() },
                hint: history::hint(summary, at),
                current: summary.id == self.chat.id,
            })
            .collect();
        let title = if listing.kept { "History" } else { "History · kept in this VM only" };
        self.overlay = Some(Overlay::picker(Which::Chats, title, choices));
    }

    /// The chat opened from /history came: the client starts again with it (one that cannot take
    /// it starts fresh, and the chat stays in the history).
    pub fn opened(&mut self, chat: Result<Chat, String>, now: Instant) {
        self.dirty = true;
        let mut chat = match chat {
            Ok(chat) => chat,
            Err(error) => {
                self.note = Some((format!("Could not open the chat: {}", text::clean(&error).trim()), now));
                return;
            }
        };
        if chat.id == self.chat.id {
            return;
        }
        if !self.switchable() {
            self.note = Some((format!("Wait until {} has started to open another chat.", self.spec.name), now));
            return;
        }
        self.save_chat();
        chat.entered(&self.spec.harness, &self.spec.name, &self.spec.model);
        let title = chat.title.clone();
        self.chat = chat;
        self.given = false;
        self.spoke = false;
        self.note = Some((
            if self.takes_context() {
                format!("Opened “{title}”: {} starts again with it.", self.spec.name)
            } else {
                format!("Opened “{title}”: {} starts again on its own; it cannot take an earlier chat.", self.spec.name)
            },
            now,
        ));
        self.effects.push(PaneEffect::Renew);
    }

    /// /new while the client starts or switches: not taken.
    pub fn wait_to_renew(&mut self, now: Instant) {
        self.note = Some((format!("Wait until {} has started to start it again.", self.spec.name), now));
        self.dirty = true;
    }

    pub fn start_failed(&mut self, error: &str) {
        self.child = None;
        self.switch_from = None;
        self.failed = true;
        self.phase = PanePhase::Ended {
            message: format!("{} could not start: {}", self.spec.name, text::clean(error).trim()),
            failed: true,
        };
        self.dirty = true;
    }

    /// A model switch began: the new model is prepared while the client runs on.
    pub fn switch_preparing(&mut self, model: &str, now: Instant) {
        self.switch_from = Some(self.phase.clone());
        self.phase = PanePhase::Switching { model: model.to_owned(), since: now, step: "Preparing".into() };
        self.dirty = true;
    }

    /// The new model could not be prepared. A client that runs on keeps its keys: the reason
    /// shows in the status line, and the switch can be tried again from Ctrl+P. Without a client
    /// (it had ended, or ended meanwhile) the pane shows its end and the reason.
    pub fn switch_refused(&mut self, model: &str, error: &str, now: Instant) {
        let reason = format!("Could not switch to {model}: {}", text::clean(error).trim());
        let before = self.switch_from.take();
        if self.child.is_some() {
            self.phase = match before {
                Some(phase @ PanePhase::Running { .. }) => phase,
                _ => PanePhase::Running { since: now },
            };
            self.note = Some((reason, now));
        } else {
            let message = match before {
                Some(PanePhase::Ended { message, .. }) => format!("{message} {reason}"),
                _ => reason,
            };
            self.failed = true;
            self.phase = PanePhase::Ended { message, failed: true };
        }
        self.dirty = true;
    }

    /// Opens the /harness panel (Ctrl+P, Switch harness, or /harness), or goes straight to
    /// `named` (/harness NAME: its questions, or the switch when it asks none). Not while the
    /// client starts or switches.
    pub fn open_harness(&mut self, named: Option<&str>, now: Instant) {
        self.overlay = None;
        if !self.switchable() {
            self.note = Some((format!("Wait until {} has started to switch harness.", self.spec.name), now));
            self.dirty = true;
            return;
        }
        match Flow::open(&self.spec, &self.spec.model, named.filter(|named| !named.trim().is_empty())) {
            Ok((_, FlowOutcome::Start(pending))) => self.start_switch(*pending, now),
            Ok((flow, _)) => self.overlay = Some(Overlay::Switch(Box::new(flow))),
            Err(error) => self.note = Some((error, now)),
        }
        self.dirty = true;
    }

    /// The switch starts: in place the client stops for it; a new VM's switch leaves it running
    /// until the launcher takes over. Secrets typed for the next harness stay in this process.
    pub fn start_switch(&mut self, pending: Pending, now: Instant) {
        self.overlay = None;
        for (id, value) in &pending.secrets {
            crate::spec::remember_secret(id, value);
        }
        let in_place = pending.mode == Where::InPlace;
        self.change_from = Some(self.phase.clone());
        let step = if in_place { format!("stopping {}", self.spec.name) } else { "asking for a new VM".into() };
        self.phase = PanePhase::Changing { name: pending.entry.name.clone(), since: now, step };
        self.change = Some(Box::new(pending.clone()));
        self.effects.push(PaneEffect::Switch(Box::new(pending)));
        self.dirty = true;
    }

    /// The switch's latest progress.
    pub fn change_step(&mut self, step: &str) {
        if let PanePhase::Changing { step: current, .. } = &mut self.phase {
            *current = step.to_owned();
        }
        self.dirty = true;
    }

    /// The switch failed: the reason shows in the status line. A new VM's switch goes back to how
    /// it was (the client kept running); in place, the run loop starts the harness before again,
    /// or Yougori's own agent when it is gone. Whether it was in place.
    pub fn change_failed(&mut self, reason: &str, now: Instant) -> bool {
        let pending = self.change.take();
        let name = pending.as_ref().map_or_else(|| "the harness".to_owned(), |p| p.entry.name.clone());
        let in_place = pending.as_ref().is_none_or(|p| p.mode == Where::InPlace);
        let before = self.change_from.take();
        if in_place {
            self.phase = PanePhase::Starting { since: now };
        } else if matches!(self.phase, PanePhase::Changing { .. }) {
            self.phase = match before {
                Some(phase @ (PanePhase::Running { .. } | PanePhase::Ended { .. })) if self.child.is_some() || matches!(phase, PanePhase::Ended { .. }) => phase,
                _ => PanePhase::Ended { message: format!("{} ended.", self.spec.name), failed: false },
            };
        }
        self.note = Some((format!("Could not switch to {name}: {}", text::clean(reason).trim()), now));
        self.dirty = true;
        in_place
    }

    /// A model was chosen while the client starts or another switch runs: it is not taken.
    pub fn wait_to_switch(&mut self, now: Instant) {
        self.note = Some((format!("Wait until {} has started to switch models.", self.spec.name), now));
        self.dirty = true;
    }

    pub fn resize(&mut self, width: u16, height: u16) {
        let (rows, cols) = pane_size(width, height);
        let rows_nz = NonZeroU16::new(rows).unwrap_or(NonZeroU16::MIN);
        let cols_nz = NonZeroU16::new(cols).unwrap_or(NonZeroU16::MIN);
        self.parser.set_size(rows_nz, cols_nz);
        // A resize takes the client's screen back to its live rows, as xterm does: stay as far
        // back as before, and keep the count in step with the screen either way.
        if self.scrollback > 0 {
            self.parser.screen_mut().set_scrollback(self.scrollback);
        }
        self.scrollback = self.parser.screen().scrollback();
        if let Some(child) = &self.child {
            child.resize(rows, cols);
        }
        self.dirty = true;
    }

    pub fn message(&mut self, message: Message, now: Instant) {
        match message {
            Message::Output(generation, bytes) if generation == self.generation => {
                self.parser.process(&bytes);
                let replies = std::mem::take(&mut self.parser.callbacks_mut().out);
                if let Some(child) = &self.child {
                    child.send(replies);
                }
                if let Some(empty) = self.parser.callbacks_mut().line.take() {
                    self.typed = if empty { Some(0) } else { Some(self.typed.filter(|n| *n > 0).unwrap_or(1)) };
                }
                self.take_said();
                self.dirty = true;
            }
            Message::Exited(generation, status) if generation == self.generation => {
                let name = &self.spec.name;
                let (message, failed) = match status {
                    // A sign-in says it in few words, so the keys after it fit.
                    Ok(status) if self.signing && status.success() => ("Signed in.".to_owned(), false),
                    Ok(_) | Err(_) if self.signing => ("The sign-in did not finish.".to_owned(), true),
                    Ok(status) if status.success() => (format!("{name} ended."), false),
                    Ok(status) => match status.signal() {
                        Some(signal) => (format!("{name} ended: {signal}."), true),
                        None => (format!("{name} ended with exit code {}.", status.exit_code()), true),
                    },
                    Err(error) => (format!("{name} ended; its status could not be read: {error}"), true),
                };
                self.child = None;
                match self.phase {
                    // The client stopped for a harness switch, or ended while a new VM is asked
                    // for: the switch says what happens next.
                    PanePhase::Quitting { .. } | PanePhase::Changing { .. } => return,
                    // The client ended while a new model is prepared: the switch goes on, and
                    // if it fails the pane shows this end instead of a client that is gone.
                    PanePhase::Switching { .. } => {
                        self.failed = failed;
                        self.switch_from = Some(PanePhase::Ended { message, failed });
                    }
                    _ => {
                        self.failed = failed;
                        self.phase = PanePhase::Ended { message, failed };
                    }
                }
                let _ = now;
                self.dirty = true;
            }
            _ => {}
        }
    }

    fn palette(&self) -> Vec<Item> {
        let name = &self.spec.name;
        let switchable = self.spec.session.switchable();
        let mut items = vec![
            Item {
                action: Action::Harness,
                label: "Switch harness".into(),
                hint: if switchable { name.clone() } else { "needs a newer Yougori".into() },
                // Not while the client starts or switches: wait for it.
                enabled: switchable && self.switchable(),
            },
            Item {
                action: Action::Model,
                label: "Switch model".into(),
                hint: self.spec.model.clone(),
                // Not while the client starts or switches: wait for it.
                enabled: self.switchable(),
            },
            Item {
                action: Action::SendCtrlP,
                label: format!("Send Ctrl+P to {name}"),
                hint: "its own shortcut".into(),
                enabled: self.running(),
            },
            Item { action: Action::History, label: "History".into(), hint: "earlier chats · /history".into(), enabled: true },
            Item { action: Action::Keys, label: "Keys".into(), hint: "every shortcut".into(), enabled: true },
        ];
        if matches!(self.phase, PanePhase::Ended { .. }) {
            items.insert(
                0,
                Item {
                    action: Action::Restart,
                    label: "Start again".into(),
                    hint: format!("start {name}"),
                    enabled: true,
                },
            );
        }
        items.push(Item { action: Action::Quit, label: "Quit".into(), hint: format!("stop {name}"), enabled: true });
        items
    }

    fn keys_help(&self) -> Vec<(String, String)> {
        let name = &self.spec.name;
        vec![
            ("/".into(), format!("at the start of a line: Yougori's commands; others go to {name}")),
            ("/harness".into(), "switch to another harness (/harness NAME)".into()),
            ("/model".into(), "switch model (/model ID)".into()),
            ("/new".into(), format!("start {name} again, fresh; this chat is kept")),
            ("/history".into(), "open an earlier chat, from any harness".into()),
            ("/help".into(), "these keys and commands".into()),
            ("ctrl+p".into(), "Yougori's menu: harness, model, keys, quit".into()),
            ("shift+pgup".into(), format!("scroll back through {name}'s output")),
            ("shift+pgdn".into(), "scroll forward again".into()),
            ("other keys".into(), format!("go to {name}")),
            ("q".into(), format!("quit, once {name} has ended")),
        ]
    }

    /// Yougori's commands offered now: /exit and /quit only once no client runs.
    fn commands(&self) -> Vec<Command> {
        let running = self.child.is_some();
        PANE_COMMANDS
            .iter()
            .filter(|(name, ..)| !(running && matches!(*name, "exit" | "quit")))
            .map(|(name, about, takes)| Command {
                name: (*name).to_owned(),
                about: about.replace("{name}", &self.spec.name),
                takes: takes.map(str::to_owned),
            })
            .collect()
    }

    /// Whether a `/` typed now opens Yougori's commands: the line the person types to the client
    /// is not known to hold anything (or no client takes keys).
    fn command_starts(&self) -> bool {
        match self.phase {
            PanePhase::Ended { .. } | PanePhase::Starting { .. } => true,
            PanePhase::Running { .. } | PanePhase::Switching { .. } => self.typed.is_none_or(|n| n == 0),
            PanePhase::Changing { .. } | PanePhase::Quitting { .. } => false,
        }
    }

    /// Runs one of Yougori's commands typed in the pane.
    fn run_command(&mut self, name: &str, argument: &str, now: Instant) {
        match name {
            "harness" => self.open_harness(Some(argument), now),
            "model" if argument.is_empty() => self.outcome(Outcome::Run(Action::Model), now),
            "model" => match self.models.iter().find(|model| model.eq_ignore_ascii_case(argument)).cloned() {
                Some(model) => self.outcome(Outcome::Pick(Which::Models, model), now),
                None => self.note = Some((format!("There is no model {argument} here. /model lists them."), now)),
            },
            "new" => self.renew(now),
            "history" => self.effects.push(PaneEffect::History),
            "help" => self.overlay = Some(Overlay::Keys { keys: self.keys_help(), scroll: 0 }),
            "exit" | "quit" => self.request_quit(),
            _ => {}
        }
        self.dirty = true;
    }

    /// /new: a running client is asked first (what it shows goes); one that has ended starts
    /// again at once; not while it starts or switches.
    fn renew(&mut self, now: Instant) {
        match self.phase {
            PanePhase::Running { .. } if self.child.is_some() => {
                let name = &self.spec.name;
                self.overlay = Some(Overlay::Confirm {
                    what: Confirmable::Renew,
                    title: format!("Start {name} again?"),
                    detail: format!("{name} stops and starts fresh; this chat is kept in /history."),
                    yes: "start again".into(),
                });
            }
            PanePhase::Ended { .. } => {
                self.new_chat();
                self.effects.push(PaneEffect::Start);
            }
            _ => self.wait_to_renew(now),
        }
    }

    /// Text typed after a `/` that names none of Yougori's commands: it goes to the client as
    /// the keys would have.
    fn pass(&mut self, text: &str) {
        if text.is_empty() {
            return;
        }
        if let Some(child) = &self.child {
            child.send(text.as_bytes().to_vec());
            self.typed = Some(self.typed.unwrap_or(0) + text.chars().count());
        }
    }

    /// A security harness's target for the header, without its scheme: the target it was given
    /// (the practice app said as such) or the targets the VM reaches. None for other harnesses.
    fn target_words(&self) -> Option<String> {
        if self.spec.category != "security" {
            return None;
        }
        let answers = &self.spec.answers;
        let target = super::draw::answer_text(&answers["target"])
            .or_else(|| (!self.spec.reach.targets.is_empty()).then(|| self.spec.reach.targets.join(", ")))?;
        let target = target
            .split(", ")
            .map(|t| t.trim_start_matches("https://").trim_start_matches("http://").trim_end_matches('/'))
            .collect::<Vec<_>>()
            .join(", ");
        Some(if answers["practice"] == serde_json::Value::Bool(true) { format!("{target} (practice app)") } else { target })
    }

    /// Whether a model can be chosen now: the client runs, or it ended.
    fn switchable(&self) -> bool {
        matches!(self.phase, PanePhase::Running { .. } | PanePhase::Ended { .. })
    }

    fn outcome(&mut self, outcome: Outcome, now: Instant) {
        match outcome {
            Outcome::Stay => {}
            Outcome::Close => self.overlay = None,
            Outcome::Command(name, argument) => {
                self.overlay = None;
                self.run_command(&name, &argument, now);
            }
            Outcome::Pass(text) => {
                self.overlay = None;
                self.pass(&text);
            }
            Outcome::Confirmed(Confirmable::Renew) => {
                self.overlay = None;
                self.new_chat();
                self.effects.push(PaneEffect::Renew);
            }
            Outcome::Pick(Which::Chats, id) => {
                self.overlay = None;
                if id != self.chat.id {
                    self.effects.push(PaneEffect::Open(id));
                }
            }
            Outcome::Run(action) => {
                self.overlay = None;
                match action {
                    Action::Harness => self.open_harness(None, now),
                    Action::Model => {
                        let choices = self
                            .models
                            .iter()
                            .map(|m| Choice {
                                id: m.clone(),
                                label: m.clone(),
                                hint: String::new(),
                                current: *m == self.spec.model,
                            })
                            .collect();
                        self.overlay = Some(Overlay::picker(Which::Models, "Switch model", choices));
                    }
                    Action::SendCtrlP => {
                        if let Some(child) = &self.child {
                            child.send(vec![0x10]);
                        }
                    }
                    Action::Keys => self.overlay = Some(Overlay::Keys { keys: self.keys_help(), scroll: 0 }),
                    Action::Restart => self.effects.push(PaneEffect::Start),
                    Action::Quit => self.request_quit(),
                    Action::NewChat => self.renew(now),
                    Action::History => self.effects.push(PaneEffect::History),
                    Action::Mode | Action::Slash(_) | Action::Details => {}
                }
            }
            Outcome::Pick(Which::Models, model) => {
                self.overlay = None;
                if !self.switchable() {
                    // The picker stayed open while the client began to start or switch.
                    self.wait_to_switch(now);
                    return;
                }
                if model == self.spec.model && self.running() {
                    return;
                }
                if self.running() {
                    let name = &self.spec.name;
                    self.overlay = Some(Overlay::Confirm {
                        detail: format!("Switching to {model} restarts {name}; what it shows now starts again."),
                        title: format!("Switch to {model}?"),
                        what: Confirmable::SwitchModel(model),
                        yes: "switch".into(),
                    });
                } else {
                    self.effects.push(PaneEffect::Restart(model));
                }
            }
            Outcome::Pick(Which::Modes | Which::Plan(_), _) => self.overlay = None,
            Outcome::Confirmed(Confirmable::Quit) => {
                self.overlay = None;
                self.effects.push(PaneEffect::Quit);
            }
            Outcome::Confirmed(Confirmable::SwitchModel(model)) => {
                self.overlay = None;
                self.effects.push(PaneEffect::Restart(model));
            }
            Outcome::Switch(pending) => self.start_switch(*pending, now),
        }
    }

    fn request_quit(&mut self) {
        if self.running() {
            let name = &self.spec.name;
            self.overlay = Some(Overlay::Confirm {
                what: Confirmable::Quit,
                title: format!("Stop {name} and quit?"),
                detail: format!("{name} is still running; quitting stops it."),
                yes: "quit".into(),
            });
        } else {
            self.effects.push(PaneEffect::Quit);
        }
    }

    pub fn key(&mut self, key: KeyEvent, now: Instant) {
        if key.kind == KeyEventKind::Release {
            return;
        }
        self.dirty = true;
        let control = key.modifiers.contains(KeyModifiers::CONTROL);
        let shift = key.modifiers.contains(KeyModifiers::SHIFT);
        if matches!(self.phase, PanePhase::Quitting { .. }) {
            return;
        }
        if let Some(overlay) = self.overlay.as_mut() {
            let outcome = overlay.key(key);
            self.outcome(outcome, now);
            return;
        }
        if control && matches!(key.code, KeyCode::Char('p') | KeyCode::Char('P')) {
            self.overlay = Some(Overlay::palette(self.palette()));
            return;
        }
        if let PanePhase::Changing { .. } = self.phase {
            // No client takes keys while the harness switches: Esc stops the switch.
            if key.code == KeyCode::Esc || (control && key.code == KeyCode::Char('c')) {
                self.effects.push(PaneEffect::CancelSwitch);
            }
            return;
        }
        let rows = usize::from(self.parser.screen().size().0.get());
        match key.code {
            KeyCode::PageUp if shift => {
                self.scroll_back(rows.saturating_sub(2).max(1) as isize);
                return;
            }
            KeyCode::PageDown if shift => {
                self.scroll_back(-(rows.saturating_sub(2).max(1) as isize));
                return;
            }
            _ => {}
        }
        let alt = key.modifiers.contains(KeyModifiers::ALT);
        if key.code == KeyCode::Char('/') && !control && !alt && !self.signing && self.command_starts() {
            self.overlay = Some(Overlay::command(self.commands(), &self.spec.name));
            return;
        }
        if let PanePhase::Ended { .. } = self.phase {
            match key.code {
                KeyCode::Char('q') | KeyCode::Char('Q') | KeyCode::Esc if self.signing => self.effects.push(PaneEffect::Back),
                KeyCode::Char('q') | KeyCode::Char('Q') | KeyCode::Esc => self.effects.push(PaneEffect::Quit),
                KeyCode::Char('c') if control => self.effects.push(PaneEffect::Quit),
                KeyCode::Enter => self.effects.push(PaneEffect::Start),
                _ => {}
            }
            return;
        }
        if control && key.code == KeyCode::Char('l') {
            // Paint the whole screen again; the client gets Ctrl+L too and redraws its own.
            self.effects.push(PaneEffect::Redraw);
        }
        if key.code == KeyCode::Enter && self.child.is_some() && self.typed != Some(0) {
            // A line sent to the client: what its screen shows from now is part of the chat.
            self.spoke = true;
        }
        if let Some(child) = &self.child {
            let application_cursor = self.parser.screen().application_cursor();
            let bytes = key_bytes(key, application_cursor);
            child.send(match child.line_erase() {
                Some(erase) => line_bytes(key, bytes, erase),
                None => bytes,
            });
            self.track(key);
            if self.scrollback > 0 {
                self.scroll_back(-(self.scrollback as isize));
            }
        }
    }

    /// What a key sent to the client does to the line the person types, as far as the pane can
    /// tell: Enter, Ctrl+C and Ctrl+U leave it empty, a character adds one, Backspace takes one
    /// away; keys that move in it (arrows, Home, Tab, Esc) leave it unknown, and the screen kit's
    /// word on its line follows anyway.
    fn track(&mut self, key: KeyEvent) {
        let control = key.modifiers.contains(KeyModifiers::CONTROL);
        let alt = key.modifiers.contains(KeyModifiers::ALT);
        let shift = key.modifiers.contains(KeyModifiers::SHIFT);
        self.typed = match key.code {
            KeyCode::Enter if !(shift || alt || control) => Some(0),
            KeyCode::Char('c' | 'u') if control => Some(0),
            // Backspace, also as the Windows console gives it (Ctrl+H).
            KeyCode::Backspace if !(alt || control) => self.typed.map(|n| n.saturating_sub(1)),
            KeyCode::Char('h') if control => self.typed.map(|n| n.saturating_sub(1)),
            KeyCode::Char('l') if control => self.typed,
            KeyCode::Char(_) if !(alt || control) => Some(self.typed.unwrap_or(0) + 1),
            KeyCode::PageUp | KeyCode::PageDown | KeyCode::Insert | KeyCode::F(_) => self.typed,
            _ => None,
        };
    }

    pub fn paste(&mut self, text: &str) {
        self.dirty = true;
        if let Some(Overlay::Command { line, .. }) = &self.overlay {
            // Lines pasted after a `/` are no command: they go to the client with what was typed.
            if text.contains(['\n', '\r']) {
                let typed = line.text().to_owned();
                self.overlay = None;
                self.pass(&typed);
                self.paste(text);
                return;
            }
        }
        if let Some(overlay) = self.overlay.as_mut() {
            overlay.paste(text);
            if let Some(passing) = overlay.passing() {
                self.overlay = None;
                self.pass(&passing);
            }
            return;
        }
        if let Some(child) = &self.child {
            child.send(paste_bytes(text, self.parser.screen().bracketed_paste()));
            let count = text.chars().filter(|c| !c.is_control()).count();
            if count > 0 {
                self.typed = Some(self.typed.unwrap_or(0) + count);
            }
        }
    }

    fn scroll_back(&mut self, by: isize) {
        let wanted = (self.scrollback as isize + by).max(0) as usize;
        self.parser.screen_mut().set_scrollback(wanted);
        self.scrollback = self.parser.screen().scrollback();
        self.dirty = true;
    }

    fn color(&self, color: vt100::Color) -> Color {
        match color {
            vt100::Color::Default => Color::Reset,
            vt100::Color::Idx(i) => self.theme.adapt(Color::Indexed(i)),
            vt100::Color::Rgb(r, g, b) => self.theme.adapt(Color::Rgb(r, g, b)),
        }
    }

    /// Draws the frame and the client's screen; returns where the cursor goes and the rows of the
    /// client's screen, which the writer scrolls when the client's output moved them.
    pub fn draw(&mut self, area: Rect, buf: &mut Buffer, now: Instant) -> Frame {
        if area.width < 4 || area.height < 3 {
            return Frame::default();
        }
        let width = area.width as usize;
        let theme = self.theme;
        // The header: Yougori, the harness, the model; the state on the right.
        let word = "Yougori";
        let mut left: Vec<ratatui::text::Span<'static>> = vec![pad(1)];
        for (i, c) in word.chars().enumerate() {
            let style =
                Style::new().fg(theme.mix(BLUE, SKY, i as f64 / word.len() as f64)).add_modifier(Modifier::BOLD);
            left.push(span(c.to_string(), style));
        }
        left.push(span("  ", Style::default()));
        let name = self.masker.mask(&self.spec.name).into_owned();
        match brand(&self.spec.harness) {
            // A security harness's name in its own colours, as its terminal draws it.
            Some((first, signature)) => {
                let last = name.chars().count().saturating_sub(1).max(1) as f64;
                for (i, c) in name.chars().enumerate() {
                    let colour = theme.mix(first, signature, i as f64 / last);
                    left.push(span(c.to_string(), Style::new().fg(colour).add_modifier(Modifier::BOLD)));
                }
            }
            None => left.push(span(name, theme.bold())),
        }
        left.push(span(" · ", theme.muted()));
        left.push(span(self.masker.mask(&crate::spec::shown_model(&self.spec.model)).into_owned(), theme.muted()));
        if let Some(target) = self.target_words() {
            left.push(span(" · target ", theme.muted()));
            left.push(span(self.masker.mask(&target).into_owned(), Style::default()));
        }
        let spinner = || span(theme::spinner(now.saturating_duration_since(self.epoch).as_millis()), theme.accent());
        let right: Vec<ratatui::text::Span<'static>> = match &self.phase {
            PanePhase::Starting { since } => vec![
                spinner(),
                span(format!(" starting {}", clock(now.saturating_duration_since(*since))), theme.muted()),
            ],
            PanePhase::Running { since } => {
                // A heartbeat: the dot dims every other second, as the clock beside it moves on.
                let beat = now.saturating_duration_since(self.epoch).as_secs() % 2 == 1;
                let dot = if beat { Style::new().fg(theme.mix(GREEN, GRAY, 0.55)) } else { theme.good() };
                vec![
                    span("● ", dot),
                    span(format!("running {}", clock(now.saturating_duration_since(*since))), theme.muted()),
                ]
            }
            PanePhase::Switching { model, step, .. } => {
                vec![spinner(), span(format!(" switching to {model}: {}", step.to_lowercase()), theme.muted())]
            }
            PanePhase::Changing { name, since, .. } => vec![
                spinner(),
                span(format!(" switching to {name} {}", clock(now.saturating_duration_since(*since))), theme.muted()),
            ],
            PanePhase::Ended { failed, .. } => vec![span(
                if *failed { "■ ended" } else { "◇ ended" },
                if *failed { theme.bad() } else { theme.good() },
            )],
            PanePhase::Quitting { .. } => vec![spinner(), span(" stopping", theme.muted())],
        };
        let right_width = text::spans_width(&right);
        let left = truncate_spans(left, width.saturating_sub(right_width + 3));
        let used = text::spans_width(&left);
        let mut header = left;
        if used + right_width + 1 < width {
            header.push(pad(width - used - right_width - 1));
            header.extend(right);
        }
        buf.set_line(area.x, area.y, &Line::from(header), area.width);

        // The client's screen.
        let pane = Rect::new(area.x, area.y + 1, area.width, area.height - 2);
        let screen = self.parser.screen();
        let (rows, cols) = screen.size();
        let (rows, cols) = (rows.get(), cols.get());
        for row in 0..rows.min(pane.height) {
            for col in 0..cols.min(pane.width) {
                let Some(cell) = screen.cell(row, col) else { continue };
                if cell.is_wide_continuation() {
                    continue;
                }
                let contents = cell.contents();
                let symbol = if contents.is_empty() || contents.chars().any(char::is_control) { " " } else { contents };
                let mut style = Style::new().fg(self.color(cell.fgcolor())).bg(self.color(cell.bgcolor()));
                if cell.bold() {
                    style = style.add_modifier(Modifier::BOLD);
                }
                if cell.dim() {
                    style = style.add_modifier(Modifier::DIM);
                }
                if cell.italic() {
                    style = style.add_modifier(Modifier::ITALIC);
                }
                if cell.underline() {
                    style = style.add_modifier(Modifier::UNDERLINED);
                }
                if cell.inverse() {
                    style = style.add_modifier(Modifier::REVERSED);
                }
                let x = pane.x + col;
                let y = pane.y + row;
                buf[(x, y)].set_symbol(symbol).set_style(style);
                if cell.is_wide() && col + 1 < pane.width {
                    buf[(x + 1, y)].reset();
                }
            }
        }
        // Nothing drawn yet, or the start failed: say so in the pane.
        let empty = screen.contents().trim().is_empty();
        let message: Option<(String, Style)> = match &self.phase {
            PanePhase::Starting { .. } if empty => Some((format!("Starting {}…", self.spec.name), theme.muted())),
            PanePhase::Switching { model, .. } if empty => {
                Some((format!("Starting {} with {model}…", self.spec.name), theme.muted()))
            }
            PanePhase::Changing { name, step, .. } => Some((format!("Switching to {name}: {step}"), theme.muted())),
            PanePhase::Ended { message, failed } if empty => {
                Some((message.clone(), if *failed { theme.bad() } else { theme.muted() }))
            }
            _ => None,
        };
        if let Some((message, style)) = message {
            let lines = wrap_text(&message, style, width.saturating_sub(4), &[pad(2)], &[pad(2)]);
            for (i, line) in lines.iter().enumerate().take(pane.height.saturating_sub(1) as usize) {
                buf.set_line(pane.x, pane.y + 1 + i as u16, line, pane.width);
            }
        }

        // The status line: what the VM reaches and the keys (as the screen's line of keys: each key
        // in sky, what it does muted), or how the client ended.
        let status_y = area.bottom() - 1;
        let keys = |spans: &mut Vec<ratatui::text::Span<'static>>, keys: &[(&str, &str)]| {
            for (key, words) in keys {
                let item = text::width(key) + 1 + text::width(words);
                if text::spans_width(spans) + 3 + item > width {
                    break;
                }
                spans.push(pad(3));
                spans.push(span(key.to_string(), theme.accent()));
                spans.push(span(format!(" {words}"), theme.muted()));
            }
        };
        let status: Vec<ratatui::text::Span<'static>> = match &self.phase {
            PanePhase::Ended { message, failed } => {
                let mut spans = vec![
                    pad(1),
                    span(if *failed { "■ " } else { "◇ " }, if *failed { theme.bad() } else { theme.good() }),
                    span(self.masker.mask(message).into_owned(), Style::default()),
                ];
                if self.signing {
                    let back = self.back.as_deref().map(crate::spec::shown_model).unwrap_or_else(|| "quit".into());
                    let back = if self.back.is_some() { format!("back to {back}") } else { back };
                    keys(&mut spans, &[("enter", "sign in again"), ("esc", &back)]);
                } else {
                    keys(&mut spans, &[("q", "quit"), ("enter", "start again"), ("/", "commands"), ("ctrl+p", "menu")]);
                }
                spans
            }
            _ => {
                let mut spans = vec![pad(1), span(super::draw::reach_words(&self.spec), theme.muted())];
                if let Some(note) = self.note_shown(now) {
                    spans.push(span(" · ", theme.muted()));
                    spans.push(span(self.masker.mask(note).into_owned(), theme.warn()));
                } else if self.scrollback > 0 {
                    spans.push(span(" · ", theme.muted()));
                    spans.push(span(format!("{} rows back", self.scrollback), theme.warn()));
                    keys(&mut spans, &[("shift+pgdn", "returns")]);
                } else if let PanePhase::Changing { .. } = self.phase {
                    spans.push(span(" · switching harness", theme.muted()));
                    keys(&mut spans, &[("esc", "to stop")]);
                } else if self.signing {
                    // A Claude sign-in stays in the VM; a ChatGPT one Yougori keeps for next time.
                    let kept = crate::spec::plan_of(&self.spec.model).is_some_and(|(plan, _)| plan.transferable);
                    let words = if kept {
                        " · sign in as the client says; Yougori keeps the sign-in on your computer for next time"
                    } else {
                        " · sign in as the client says; the sign-in stays in this VM"
                    };
                    spans.push(span(words, theme.muted()));
                } else {
                    keys(&mut spans, &[("/", "commands"), ("ctrl+p", "menu"), ("shift+pgup", "scroll back")]);
                }
                spans
            }
        };
        buf.set_line(area.x, status_y, &Line::from(truncate_spans(status, width)), area.width);

        // The palette or a confirmation over the bottom of the pane.
        let mut cursor = None;
        if let Some(overlay) = &self.overlay {
            let max_rows = (pane.height as usize * 6 / 10).max(4);
            let (lines, at) = overlay.lines(&theme, width, max_rows);
            let count = lines.len().min(pane.height as usize);
            let top = pane.bottom() - count as u16;
            for y in top..pane.bottom() {
                for x in pane.left()..pane.right() {
                    buf[(x, y)].reset();
                }
            }
            for (i, line) in lines.iter().take(count).enumerate() {
                buf.set_line(pane.x, top + i as u16, line, pane.width);
            }
            if let Some((row, column)) = at {
                cursor = Some(Position::new(pane.x + column.min(width - 1) as u16, top + row as u16));
            }
        } else if self.running() && self.scrollback == 0 && !screen.hide_cursor() {
            let (row, col) = screen.cursor_position();
            if row < pane.height && col < pane.width {
                cursor = Some(Position::new(pane.x + col, pane.y + row));
            }
        }
        // The client draws text as it likes (inside frames, cut short): its screen is read row by
        // row as well; the header and the status line are ours.
        self.masker.mask_screen(buf, pane);
        self.masker.mask_buffer(buf, area);
        // The writer finds by itself how far the client's output moved the rows.
        let scroll =
            (pane.height >= 2).then_some(ScrollRegion { top: pane.y, bottom: pane.bottom(), first_line: None });
        Frame { cursor, scroll }
    }
}

/// How long the screen waits for a client to end after asking it to.
pub const STOP_WAIT: Duration = Duration::from_secs(3);
/// How long a client asked to stop for the next one (/new, another model) has to end, stopping
/// what it started (a database, a server), before the next one starts anyway.
pub const RENEW_WAIT: Duration = Duration::from_secs(15);
/// How long a note stays in the status line.
pub const NOTE_TIME: Duration = Duration::from_secs(10);

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keys_become_the_bytes_a_terminal_sends() {
        let key = |code, modifiers| KeyEvent::new(code, modifiers);
        assert_eq!(key_bytes(key(KeyCode::Char('a'), KeyModifiers::NONE), false), b"a");
        assert_eq!(key_bytes(key(KeyCode::Char('c'), KeyModifiers::CONTROL), false), [3]);
        assert_eq!(key_bytes(key(KeyCode::Char('b'), KeyModifiers::ALT), false), b"\x1bb");
        assert_eq!(key_bytes(key(KeyCode::Up, KeyModifiers::NONE), false), b"\x1b[A");
        assert_eq!(key_bytes(key(KeyCode::Up, KeyModifiers::NONE), true), b"\x1bOA");
        assert_eq!(key_bytes(key(KeyCode::Right, KeyModifiers::CONTROL), false), b"\x1b[1;5C");
        assert_eq!(key_bytes(key(KeyCode::Enter, KeyModifiers::NONE), false), b"\r");
        assert_eq!(key_bytes(key(KeyCode::Enter, KeyModifiers::SHIFT), false), b"\x1b\r");
        assert_eq!(key_bytes(key(KeyCode::PageUp, KeyModifiers::NONE), false), b"\x1b[5~");
        assert_eq!(key_bytes(key(KeyCode::F(5), KeyModifiers::NONE), false), b"\x1b[15~");
        assert_eq!(paste_bytes("a\nb", true), b"\x1b[200~a\rb\x1b[201~");
        assert_eq!(paste_bytes("a\r\nb\x1b", false), b"a\rb");
    }

    #[test]
    fn a_line_read_whole_takes_backspace_as_its_erase_and_no_codes() {
        let key = |code, modifiers| KeyEvent::new(code, modifiers);
        let line = |k: KeyEvent| line_bytes(k, key_bytes(k, false), 0x7f);
        // Backspace as the Windows console gives it (Ctrl+H), as other terminals give it, and with Ctrl.
        assert_eq!(line(key(KeyCode::Char('h'), KeyModifiers::CONTROL)), [0x7f]);
        assert_eq!(line(key(KeyCode::Backspace, KeyModifiers::NONE)), [0x7f]);
        assert_eq!(line(key(KeyCode::Backspace, KeyModifiers::CONTROL)), [0x7f]);
        assert_eq!(line_bytes(key(KeyCode::Backspace, KeyModifiers::NONE), vec![0x7f], 0x08), [0x08]);
        // Keys a line cannot use send nothing instead of their codes.
        for code in [KeyCode::Left, KeyCode::Up, KeyCode::Delete, KeyCode::Home, KeyCode::End, KeyCode::F(5), KeyCode::Esc] {
            assert_eq!(line(key(code, KeyModifiers::NONE)), Vec::<u8>::new(), "{code:?}");
        }
        assert_eq!(line(key(KeyCode::Char('b'), KeyModifiers::ALT)), Vec::<u8>::new());
        // The rest as before; Shift+Enter sends the line.
        assert_eq!(line(key(KeyCode::Char('a'), KeyModifiers::NONE)), b"a");
        assert_eq!(line(key(KeyCode::Char('c'), KeyModifiers::CONTROL)), [3]);
        assert_eq!(line(key(KeyCode::Char('u'), KeyModifiers::CONTROL)), [0x15]);
        assert_eq!(line(key(KeyCode::Enter, KeyModifiers::SHIFT)), b"\r");
        assert_eq!(line(key(KeyCode::Tab, KeyModifiers::NONE)), b"\t");
        // A terminal without an erase character keeps the key's own byte.
        assert_eq!(line_bytes(key(KeyCode::Char('h'), KeyModifiers::CONTROL), vec![0x08], 0), [0x08]);
    }

    #[cfg(unix)]
    #[test]
    fn the_pane_reads_whether_its_client_reads_whole_lines() {
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let mut spec: EngineSpec = serde_json::from_value(serde_json::json!({
            "harness": "test", "name": "Test", "category": "coding", "protocol": "terminal",
            "argv": ["/bin/sh", "-c", "stty -icanon; echo raw; sleep 1; stty icanon; echo line; sleep 5"],
            "cwd": "/", "model": "m", "state_dir": "/tmp"
        }))
        .unwrap();
        spec.env.insert("PATH".into(), "/usr/bin:/bin".into());
        let child = spawn(&spec, 10, 40, 1, None, Depth::None, tx).unwrap();
        let mut seen = String::new();
        let started = Instant::now();
        let mut modes = Vec::new();
        while started.elapsed() < Duration::from_secs(4) && !seen.contains("line") {
            if let Ok(Message::Output(_, bytes)) = rx.try_recv() {
                seen.push_str(&String::from_utf8_lossy(&bytes));
                if seen.contains("raw") && modes.is_empty() {
                    modes.push(child.line_erase());
                }
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        std::thread::sleep(Duration::from_millis(200));
        modes.push(child.line_erase());
        assert_eq!(modes.first(), Some(&None), "raw mode: the client reads keys itself");
        assert!(matches!(modes.last(), Some(Some(_))), "canonical mode: {modes:?} after {seen:?}");
    }

    #[test]
    fn the_pane_answers_terminal_questions() {
        let mut parser = parser(10, 40);
        parser.process(b"hello\x1b[6n\x1b[c\x1b]11;?\x07");
        let replies = String::from_utf8(std::mem::take(&mut parser.callbacks_mut().out)).unwrap();
        assert!(replies.starts_with("\x1b[1;6R"), "{replies:?}");
        assert!(replies.contains("\x1b[?62;22c"));
        assert!(replies.contains("\x1b]11;rgb:0000/0000/0000"));
    }
}
