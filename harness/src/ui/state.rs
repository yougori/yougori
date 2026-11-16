//! The screen's state and how engine events change it. Keys are in `keys.rs`, drawing in
//! `draw.rs`; the run loop (`mod.rs`) carries out the [`Effect`]s this state asks for.
use super::dialog::Dialog;
use super::input::{Editor, History};
use super::mask::Masker;
use super::overlay::{Action, Choice, Confirmable, Item, Outcome, Overlay, Which};
use super::switcher::{Flow, FlowOutcome, Mode as Where, Pending};
use super::text::clean;
use super::theme::Theme;
use super::transcript::{Key, Transcript};
use crate::history::{self, Chat, Listing};
use crate::protocol::{Command, Event, Level, Mode, ModelSwitch, PlanEntry, SlashCommand, StopReason, ToolCall, Usage};
use crate::spec::EngineSpec;
use std::collections::{HashSet, VecDeque};
use std::time::{Duration, Instant};

/// Ctrl+C twice within this quits.
pub const QUIT_WINDOW: Duration = Duration::from_secs(2);

#[derive(Clone, Debug, PartialEq)]
pub enum Phase {
    /// The engine is starting (first start, or again after it stopped).
    Starting {
        since: Instant,
    },
    Ready,
    /// Switching models by restarting the engine.
    Switching {
        model: String,
        since: Instant,
        step: String,
    },
    /// Switching harness (/harness): `step` is the switch's latest progress.
    Changing {
        name: String,
        since: Instant,
        step: String,
    },
    /// The engine stopped or could not start; the screen stays until the person quits.
    Stopped {
        title: String,
        message: String,
        failed: bool,
    },
    /// The person quit; the engine is being stopped.
    Quitting {
        since: Instant,
    },
}

/// What the run loop does for the state.
#[derive(Clone, Debug, PartialEq)]
pub enum Effect {
    Send(Command),
    /// Start the spec's engine (again).
    Start,
    /// Switch to this model by restarting the engine through the spec's `respec`.
    Restart(String),
    /// Switch harness (/harness): in place the engine stops first, then the switch command runs.
    Switch(Box<Pending>),
    /// Esc while switching harness: the switch command is asked to stop.
    CancelSwitch,
    /// Paint the whole screen again.
    Redraw,
    Quit,
    /// Save the chat on the person's computer (history.rs).
    Save(Box<Chat>),
    /// /history: list the chats kept.
    History,
    /// Open this chat from /history.
    Open(String),
    /// Ask the model for its notes on the earlier part of the chat (`text`), which a prompt waits
    /// for before it goes to the engine.
    Notes { text: String },
    /// Start the engine again, fresh (a new chat for an engine without a /new of its own).
    Renew,
    /// Read this inference's models afresh (/model, or while there is no model yet).
    Models,
    /// The person chose a plan (their own subscription) and how to sign in: the launcher opens its
    /// sites and sends the sign-in, then the engine restarts with `model`.
    Subscribe { plan: String, using: String, model: String },
}

/// A prompt waiting for the model's notes on the earlier part of the chat before it goes to the
/// engine: what the person typed, and when it was sent.
#[derive(Clone, Debug, PartialEq)]
pub struct Waiting {
    pub prompt: String,
    pub since: Instant,
}

pub struct Turn {
    pub started: Instant,
    pub stopping: bool,
    /// The prompt that started it.
    pub prompt: String,
    /// A resumed session's history is being replayed into a cleared conversation.
    pub replaying: bool,
}

/// Whether a prompt asks to resume an earlier session (its history is then replayed).
fn resumes(prompt: &str) -> bool {
    let prompt = prompt.trim();
    prompt == "/sessions" || prompt.starts_with("/sessions ")
}

/// Where the conversation view is.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Scroll {
    /// Showing the newest lines as they come.
    Follow,
    /// Held at a line of an entry (None: the header).
    At { key: Option<Key>, offset: usize },
}

/// The conversation view as last drawn, for scrolling.
#[derive(Clone, Debug, Default)]
pub struct View {
    /// Each block's key (None for the header), first line and height.
    pub blocks: Vec<(Option<Key>, usize, usize)>,
    pub total: usize,
    pub height: usize,
    pub top: usize,
}

pub struct App {
    pub spec: EngineSpec,
    pub theme: Theme,
    pub masker: Masker,
    pub version: String,
    pub epoch: Instant,
    /// The screen started the engine itself (and can start it again).
    pub owns_engine: bool,

    pub phase: Phase,
    pub name: String,
    pub model: String,
    pub models: Vec<String>,
    pub model_switch: ModelSwitch,
    pub session: Option<String>,
    pub info: Option<String>,
    pub ready_once: bool,

    pub transcript: Transcript,
    pub plan: Vec<PlanEntry>,
    /// Findings by severity: critical, high, medium, low, info.
    pub findings: [usize; 5],
    pub usage: Usage,
    pub modes: Vec<Mode>,
    pub mode: Option<String>,
    pub commands: Vec<SlashCommand>,
    pub turn: Option<Turn>,
    /// Prompts sent in this conversation, and the one the plan was last updated in.
    pub turns: u64,
    pub plan_turn: u64,
    /// Something was said in this conversation (the keys line under the box goes away).
    pub conversed: bool,
    pub queued: VecDeque<String>,

    pub dialogs: VecDeque<Dialog>,
    pub overlay: Option<Overlay>,

    pub input: Editor,
    pub history: History,
    pub completion: usize,
    pub completion_closed: Option<String>,

    pub details: bool,
    pub scroll: Scroll,
    pub view: View,
    /// The prompt box's text width, from the last frame.
    pub input_width: usize,
    pub quit_armed: Option<Instant>,
    pub failed: bool,
    pub effects: Vec<Effect>,
    pub dirty: bool,
    /// Whether the terminal tells Shift+Enter from Enter; the keys line and the keys help then
    /// name it for a new line, else Ctrl+J.
    pub shift_enter: bool,
    /// The last prompt this screen sent, to drop an engine's echo of it, and what the
    /// conversation shows of it (without the earlier chat it may carry).
    echo: Option<String>,
    echo_shown: Option<String>,
    /// The engine was asked for a new session of its own for a new or opened chat: its word that
    /// one started is not shown.
    quiet_new: bool,
    /// A save already failed this session (said once).
    save_failed: bool,
    /// While a restart switch is prepared: the phase to go back to if the new model cannot be.
    switch_from: Option<Phase>,
    /// Yougori's own slash commands, which the screen handles itself (never the engine).
    pub own: Vec<SlashCommand>,
    /// The harness switch under way, and the phase to go back to when a new VM's switch fails
    /// (the engine kept running).
    pub change: Option<Box<Pending>>,
    change_from: Option<Phase>,

    /// The chat as it goes, kept on the person's computer (history.rs).
    pub chat: Chat,
    /// How many of the chat's turns the running engine has: the rest reach it with the next
    /// prompt (a chat it took over), and whether it has the chat's terminal screen.
    pub given: usize,
    pub screen_given: bool,
    /// The reply message last recorded, and the tool calls recorded this turn.
    chat_message: Option<String>,
    chat_tools: HashSet<String>,
    /// A prompt waiting for the model's notes on the earlier chat.
    pub waiting: Option<Waiting>,
    /// Whether the person's computer keeps the chats (None until a save or a listing says).
    pub kept: Option<bool>,
    /// The chat this session goes on with is being opened (a /harness switch into a new VM brought
    /// it): prompts wait for it, so that the engine gets it with the first.
    pub resuming: bool,
    /// The inference's models are being read afresh (each time /model opens, and now and then
    /// while there is no model yet), when they last were, and whether the model picker opens
    /// once they come.
    models_reading: bool,
    models_read: Option<Instant>,
    models_wanted: bool,
    /// The models the running engine was set up with (its Ready): one read since then needs a
    /// restart to switch to, as the engine does not know it.
    engine_models: Vec<String>,
}

/// How often the inference's models are read again while the session has no model yet.
pub const MODELS_AGAIN: Duration = Duration::from_secs(20);

/// Yougori's own slash commands: name, what it does, what to type after it.
pub const OWN_COMMANDS: [(&str, &str, Option<&str>); 7] = [
    ("harness", "switch to another harness", Some("name")),
    ("model", "switch model", Some("id")),
    ("new", "start a new chat (this one is kept)", None),
    ("history", "open an earlier chat", None),
    ("help", "commands and keys", None),
    ("exit", "end the session", None),
    ("quit", "end the session", None),
];

impl App {
    pub fn new(spec: EngineSpec, theme: Theme, masker: Masker, owns_engine: bool, now: Instant) -> App {
        let models = if spec.models.is_empty() && !spec.model.is_empty() { vec![spec.model.clone()] } else { spec.models.clone() };
        let chat = Chat::new(&spec.harness, &spec.name, &spec.model);
        App {
            chat,
            given: 0,
            screen_given: false,
            chat_message: None,
            chat_tools: HashSet::new(),
            waiting: None,
            kept: None,
            resuming: false,
            models_reading: false,
            models_read: None,
            models_wanted: false,
            engine_models: Vec::new(),
            name: spec.name.clone(),
            model: spec.model.clone(),
            model_switch: spec.model_switch,
            models,
            spec,
            theme,
            masker,
            version: env!("CARGO_PKG_VERSION").to_owned(),
            epoch: now,
            owns_engine,
            phase: Phase::Starting { since: now },
            session: None,
            info: None,
            ready_once: false,
            transcript: Transcript::default(),
            plan: Vec::new(),
            findings: [0; 5],
            usage: Usage::default(),
            modes: Vec::new(),
            mode: None,
            commands: Vec::new(),
            turn: None,
            turns: 0,
            plan_turn: 0,
            conversed: false,
            queued: VecDeque::new(),
            dialogs: VecDeque::new(),
            overlay: None,
            input: Editor::default(),
            history: History::default(),
            completion: 0,
            completion_closed: None,
            details: false,
            scroll: Scroll::Follow,
            view: View::default(),
            input_width: 60,
            quit_armed: None,
            failed: false,
            effects: Vec::new(),
            dirty: true,
            shift_enter: true,
            echo: None,
            echo_shown: None,
            quiet_new: false,
            save_failed: false,
            switch_from: None,
            own: OWN_COMMANDS
                .iter()
                .map(|(name, what, hint)| SlashCommand {
                    name: (*name).to_owned(),
                    description: (*what).to_owned(),
                    hint: hint.map(str::to_owned),
                })
                .collect(),
            change: None,
            change_from: None,
        }
    }

    pub fn security(&self) -> bool {
        self.spec.category == "security"
    }

    /// Something runs: a turn, or tool calls and progress the engine started on its own.
    pub fn busy(&self) -> bool {
        self.turn.is_some() || self.transcript.live()
    }

    /// Since when the current work runs.
    pub fn busy_since(&self) -> Option<Instant> {
        self.turn.as_ref().map(|t| t.started).or_else(|| self.transcript.live_since())
    }

    pub fn stopped(&self) -> bool {
        matches!(self.phase, Phase::Stopped { .. })
    }

    /// Whether the screen changes by itself (spinners, clocks, the quit hint).
    pub fn animating(&self, now: Instant) -> bool {
        // The quit hint gets one more frame after it expires, to take it away.
        self.busy()
            || matches!(
                self.phase,
                Phase::Starting { .. } | Phase::Switching { .. } | Phase::Changing { .. } | Phase::Quitting { .. }
            )
            || self
                .quit_armed
                .is_some_and(|t| now.saturating_duration_since(t) < QUIT_WINDOW + Duration::from_millis(300))
    }

    /// The first screen: nothing said yet in this conversation.
    pub fn first_screen(&self) -> bool {
        !self.conversed
    }

    /// How much of the context window the conversation takes, for the status line. The window
    /// is the smaller of the engine's and the model's. An engine that does not know the model
    /// reports its own default (Gemini CLI reports 1,048,576 tokens), but the model holds only
    /// the window in the spec.
    pub fn context_share(&self) -> Option<f64> {
        let tokens = self.usage.context_tokens?;
        let window = [self.usage.context_window, Some(u64::from(self.spec.context_window))]
            .into_iter()
            .flatten()
            .filter(|window| *window > 0)
            .min()?;
        Some(tokens as f64 / window as f64)
    }

    pub fn exit_code(&self) -> i32 {
        i32::from(self.failed)
    }

    pub fn send(&mut self, command: Command) {
        self.effects.push(Effect::Send(command));
    }

    /// Applies one engine event.
    pub fn apply(&mut self, event: Event, now: Instant) {
        self.dirty = true;
        match event {
            Event::Ready(ready) => {
                let again = self.ready_once;
                if !ready.name.trim().is_empty() {
                    self.name = ready.name;
                }
                if !ready.model.trim().is_empty() {
                    self.model = ready.model;
                }
                if !ready.models.is_empty() {
                    self.models = ready.models;
                }
                if !self.model.is_empty() && !self.models.contains(&self.model) {
                    self.models.insert(0, self.model.clone());
                }
                self.engine_models = self.models.clone();
                if self.model.is_empty() && !self.ready_once {
                    self.transcript.notice(Level::Info, &self.no_model_words());
                }
                self.model_switch = ready.model_switch;
                self.chat.model = self.model.clone();
                let quiet = std::mem::take(&mut self.quiet_new);
                if again && ready.session.is_some() && self.session.is_some() && ready.session != self.session && !quiet {
                    self.transcript.divider("A new session started");
                }
                self.session = ready.session;
                self.info = ready.info.map(|i| clean(&i).replace('\n', " ")).filter(|i| !i.trim().is_empty());
                self.phase = Phase::Ready;
                self.ready_once = true;
                self.failed = false;
                if self.turn.take().is_some() {
                    self.transcript.end_turn();
                }
                self.dialogs.clear();
                self.send_queued(now);
            }
            Event::Message { message, text, parent } => {
                self.conversed = true;
                self.replay_starts();
                self.transcript.message(&message, &text, parent.as_deref());
                // The chat keeps the harness's own replies, not its subagents' (they are in the
                // tools it ran), a replayed session's, nor what the engine's own commands say.
                if parent.is_none() && self.chat_turn() {
                    let separate = self.chat_message.as_deref().is_some_and(|last| last != message);
                    self.chat.reply(&text, separate);
                    self.chat_message = Some(message);
                    self.given = self.chat.turns.len();
                }
            }
            Event::Reasoning { message, text, parent } => {
                self.replay_starts();
                self.transcript.reasoning(&message, &text, parent.as_deref());
            }
            Event::User { text } => {
                let shown = self.echo_shown.clone().unwrap_or_else(|| text.clone());
                let echoed = self.echo.as_deref().is_some_and(|sent| sent.trim() == text.trim())
                    && self.transcript.last_user_text().is_some_and(|last| last.trim() == clean(&shown).trim());
                if echoed {
                    self.echo = None;
                    self.echo_shown = None;
                } else {
                    self.conversed = true;
                    self.replay_starts();
                    self.transcript.user(&text);
                }
            }
            Event::ToolCall(call) => {
                self.replay_starts();
                self.transcript.tool_call(&call, now);
                self.settled(&call.id, Some(call.status), now);
                if call.parent.is_none() && self.chat_turn() && self.chat_tools.insert(call.id.clone()) {
                    self.chat.tool(&clean(&call.title));
                    self.given = self.chat.turns.len();
                }
            }
            Event::ToolUpdate(update) => {
                self.settled(&update.id, update.status, now);
                if !self.transcript.tool_update(&update, now) {
                    // An update for a call never announced: show it as a call of its own.
                    let call = ToolCall {
                        id: update.id.clone(),
                        title: update.title.clone().unwrap_or_else(|| "Tool call".into()),
                        status: update.status.unwrap_or_default(),
                        content: update.content.clone().unwrap_or_default(),
                        locations: update.locations.clone().unwrap_or_default(),
                        ..Default::default()
                    };
                    self.transcript.tool_call(&call, now);
                }
            }
            Event::Permission(request) => self.dialogs.push_back(Dialog::permission(request, now)),
            Event::Question(request) => self.dialogs.push_back(Dialog::question(request, now)),
            Event::Plan { entries } => {
                self.plan = entries;
                self.plan_turn = self.turns;
            }
            Event::Finding(finding) => {
                self.findings[finding.severity as usize] += 1;
                self.transcript.finding(&finding);
            }
            Event::Report(report) => self.transcript.report(&report),
            Event::Progress(progress) => self.transcript.progress(&progress, now),
            Event::Usage(usage) => {
                let u = &mut self.usage;
                u.context_tokens = usage.context_tokens.or(u.context_tokens);
                u.context_window = usage.context_window.or(u.context_window);
                u.input_tokens = usage.input_tokens.or(u.input_tokens);
                u.output_tokens = usage.output_tokens.or(u.output_tokens);
            }
            Event::Model { model } => {
                if !model.trim().is_empty() && model != self.model {
                    self.transcript.notice(Level::Info, &format!("Now using {model}."));
                    if !self.models.contains(&model) {
                        self.models.push(model.clone());
                    }
                    self.chat.model = model.clone();
                    self.model = model;
                }
            }
            Event::Modes { current, available } => {
                self.mode = Some(current).filter(|m| !m.is_empty());
                self.modes = available;
            }
            Event::Commands { commands } => self.commands = commands,
            Event::TurnEnd { reason, detail } => self.turn_ended(reason, detail, now),
            // What the engine says of the new session Yougori asked it for (a new or opened chat)
            // is not shown: the screen has said it.
            Event::Notice { level, text } => {
                if !(self.quiet_new && self.turn.as_ref().is_some_and(|t| t.prompt == "/new")) || level != Level::Info {
                    self.transcript.notice(level, &text)
                }
            }
            Event::Exit { code, message } => {
                let failed = code != Some(0);
                let mut message = clean(&message).trim().to_owned();
                if failed && message.is_empty() {
                    if let Some(code) = code {
                        message = format!("Exit code {code}.");
                    }
                }
                let title = if failed { format!("{} stopped", self.name) } else { format!("{} ended", self.name) };
                self.stop(title, message, failed);
            }
        }
    }

    fn turn_ended(&mut self, reason: StopReason, detail: Option<String>, now: Instant) {
        self.transcript.end_turn();
        let ended = self.turn.take();
        let replayed = ended.as_ref().is_some_and(|turn| turn.replaying);
        if ended.as_ref().is_some_and(|turn| turn.prompt == "/new") {
            // The new session Yougori asked for has started.
            self.quiet_new = false;
        }
        self.dialogs.clear();
        self.chat_message = None;
        self.chat_tools.clear();
        if !replayed {
            self.save_chat();
        }
        // The engine's own words when it gives them, else a plain default.
        let detail = detail.map(|d| clean(&d).trim().to_owned()).filter(|d| !d.is_empty());
        let said = |default: &str| detail.clone().unwrap_or_else(|| default.to_owned());
        match reason {
            StopReason::EndTurn => {}
            StopReason::Cancelled => self.transcript.notice(Level::Info, &said("Stopped.")),
            StopReason::MaxSteps => {
                self.transcript.notice(Level::Warn, &said("Step limit reached. Send a message to continue."))
            }
            StopReason::MaxTokens => self.transcript.notice(Level::Warn, &said("The reply reached the output limit.")),
            StopReason::Refused => self.transcript.notice(Level::Warn, &said("The model declined.")),
            StopReason::Error => {
                // An engine that already said it in an error notice is not repeated.
                let shown = match &detail {
                    Some(d) => self.transcript.recent_notice(Level::Error, d),
                    None => false,
                };
                if !shown {
                    self.transcript.notice(Level::Error, &said("The turn failed."));
                }
            }
        }
        if matches!(reason, StopReason::Cancelled | StopReason::Error) {
            self.unqueue();
        } else {
            self.send_queued(now);
        }
    }

    /// Whether what the engine says now belongs to the chat: no replayed session, and no turn of an
    /// engine's own command (/new, /compact).
    fn chat_turn(&self) -> bool {
        !self.turn.as_ref().is_some_and(|t| t.replaying || t.prompt.trim_start().starts_with('/'))
    }

    /// A resumed session's history starts: the conversation shown is replaced by it.
    fn replay_starts(&mut self) {
        let starts = self.turn.as_ref().is_some_and(|t| resumes(&t.prompt) && !t.replaying);
        if starts {
            self.transcript.clear();
            self.plan.clear();
            self.findings = [0; 5];
            self.usage = Usage::default();
            self.scroll = Scroll::Follow;
            if let Some(turn) = self.turn.as_mut() {
                turn.replaying = true;
            }
        }
    }

    /// A tool call moved on: a permission still asked for it was settled by another answer
    /// (an "always" rule), so its dialog goes.
    fn settled(&mut self, tool: &str, status: Option<crate::protocol::ToolStatus>, now: Instant) {
        use crate::protocol::ToolStatus;
        if matches!(status, Some(ToolStatus::InProgress | ToolStatus::Completed | ToolStatus::Failed)) {
            let front = self.dialogs.front().map(|d| d.id().to_owned());
            self.dialogs.retain(
                |d| !matches!(d, Dialog::Permission { request, .. } if request.tool_call.as_deref() == Some(tool)),
            );
            if self.dialogs.front().map(Dialog::id) != front.as_deref() {
                self.front_shown(now);
            }
        }
    }

    /// The dialog at the front shows from now (it came forward, or what covered it closed): keys
    /// that answer wait its settle time, so a press meant for what was there before cannot
    /// answer a request the person has not seen.
    pub fn front_shown(&mut self, now: Instant) {
        if let Some(dialog) = self.dialogs.front_mut() {
            dialog.show(now);
        }
    }

    /// The engine stopped: what it said stays on screen until the person quits, and in the
    /// conversation after that.
    pub fn stop(&mut self, title: String, message: String, failed: bool) {
        self.dirty = true;
        if self.turn.take().is_some() || self.transcript.live() {
            self.transcript.end_turn();
        }
        self.dialogs.clear();
        let record = if message.is_empty() { format!("{title}.") } else { format!("{title}: {message}") };
        self.transcript.notice(if failed { Level::Error } else { Level::Info }, &record);
        self.failed = failed;
        self.phase = Phase::Stopped { title, message, failed };
        self.unqueue();
    }

    /// The engine's events ended without an Exit.
    pub fn engine_closed(&mut self) {
        if matches!(self.phase, Phase::Starting { .. } | Phase::Ready) {
            self.stop(format!("{} stopped", self.name), "It ended without saying why.".into(), true);
        }
    }

    pub fn start_failed(&mut self, error: &str) {
        self.stop(format!("{} could not start", self.name), clean(error).trim().to_owned(), true);
    }

    /// The engine's start began (again). A fresh engine has none of the chat: it gets it with the
    /// next prompt.
    pub fn starting(&mut self, now: Instant) {
        self.phase = Phase::Starting { since: now };
        self.given = 0;
        self.screen_given = false;
        self.dirty = true;
    }

    fn send_queued(&mut self, now: Instant) {
        if self.turn.is_none() && self.phase == Phase::Ready && !self.resuming {
            if let Some(text) = self.queued.pop_front() {
                self.send_prompt(text, now);
            }
        }
    }

    /// Queued prompts go back into the box, to send again or change.
    fn unqueue(&mut self) {
        if self.queued.is_empty() {
            return;
        }
        let mut parts: Vec<String> = self.queued.drain(..).collect();
        if !self.input.is_empty() {
            parts.push(self.input.text().to_owned());
        }
        self.input.set(&parts.join("\n\n"));
    }

    fn offers(&self, command: &str) -> bool {
        self.commands.iter().any(|c| c.name == command)
    }

    pub fn send_prompt(&mut self, text: String, now: Instant) {
        if self.model.is_empty() {
            // Nothing runs before a model is chosen: what was typed stays in the box.
            if self.input.text().trim().is_empty() {
                self.input.set(&text);
            }
            self.transcript.notice(Level::Info, &self.no_model_words());
            self.dirty = true;
            return;
        }
        self.transcript.user(&text);
        self.conversed = true;
        self.turns += 1;
        self.turn = Some(Turn { started: now, stopping: false, prompt: text.clone(), replaying: false });
        self.scroll = Scroll::Follow;
        self.dirty = true;
        // The engine's own commands (/compact, /review) go to it as typed, and are no part of the
        // chat.
        if text.trim_start().starts_with('/') {
            self.deliver(text, None);
            return;
        }
        let before = self.chat.turns.len();
        let unseen = self.given < before || (!self.chat.screen.trim().is_empty() && !self.screen_given);
        self.chat.user(&text);
        if !unseen {
            self.given = self.chat.turns.len();
            self.deliver(text, None);
            return;
        }
        // A chat this engine has not seen (it took the chat over): the earlier turns go with the
        // prompt, the oldest as the model's notes when they do not fit whole.
        let turns = &self.chat.turns[self.given.min(before)..before];
        let budget = history::budget(self.spec.context_window);
        let notes_model = self.notes_model();
        if history::needs_notes(&self.chat, turns, budget) && notes_model.is_some() {
            let older = history::newest(turns, budget / 2);
            // Within the window of the model that writes the notes.
            let own = notes_model.as_deref() == Some(self.model.as_str());
            let room = history::notes_room(self.spec.context_window, self.spec.max_output, own);
            let text_for_notes = history::transcript(&turns[..older], &self.chat.screen, room);
            self.transcript.notice(Level::Info, &format!("Compacting the earlier chat for {}…", self.name));
            self.waiting = Some(Waiting { prompt: text, since: now });
            self.effects.push(Effect::Notes { text: text_for_notes });
            return;
        }
        let context = history::context(&self.chat, turns, budget, None);
        self.given = self.chat.turns.len();
        self.screen_given = true;
        self.deliver(text, context);
    }

    /// The prompt goes to the engine, after the earlier chat when it carries it.
    fn deliver(&mut self, typed: String, context: Option<String>) {
        let sent = match &context {
            Some(context) => history::with_context(context, &typed),
            None => typed.clone(),
        };
        self.echo = Some(sent.clone());
        self.echo_shown = Some(typed);
        self.send(Command::Prompt { text: sent });
    }

    /// The model that writes the notes on an earlier chat: the one in use when the relay serves
    /// it (this inference's), else the first of this inference's.
    pub fn notes_model(&self) -> Option<String> {
        let served = |model: &str| self.spec.models.iter().any(|m| m == model) && !crate::spec::subscribed(model);
        if served(&self.model) {
            return Some(self.model.clone());
        }
        self.spec.models.iter().find(|model| !crate::spec::subscribed(model)).cloned()
    }

    /// The model's notes on the earlier chat came (or not): the prompt waiting for them goes to the
    /// engine with the earlier chat. Without notes the newest turns go whole and the older are
    /// left out, as the person is told.
    pub fn noted(&mut self, notes: Result<String, String>, now: Instant) {
        let Some(waiting) = self.waiting.take() else { return };
        self.dirty = true;
        let notes = match notes {
            Ok(notes) => Some(notes),
            Err(error) => {
                self.transcript.notice(
                    Level::Warn,
                    &format!("Could not compact the earlier chat ({}): its newest part goes whole, the rest is left out.", clean(&error).trim()),
                );
                None
            }
        };
        let last = self.chat.turns.len().saturating_sub(1);
        let turns = self.chat.turns[self.given.min(last)..last].to_vec();
        let context = history::context(&self.chat, &turns, history::budget(self.spec.context_window), notes.as_deref());
        self.given = self.chat.turns.len();
        self.screen_given = true;
        let _ = now;
        self.deliver(waiting.prompt, context);
    }

    /// Enter in the prompt box.
    pub fn submit(&mut self, now: Instant) {
        let text = self.input.text().trim_end().to_owned();
        if text.trim().is_empty() {
            return;
        }
        self.history.push(&text);
        self.input.clear();
        self.completion_closed = None;
        if self.own_command(&text, now) {
            self.dirty = true;
            return;
        }
        match self.phase {
            // While the chat this session goes on with opens, prompts wait for it.
            Phase::Ready if self.turn.is_none() && !self.resuming => self.send_prompt(text, now),
            Phase::Ready | Phase::Starting { .. } | Phase::Switching { .. } | Phase::Changing { .. } => {
                self.queued.push_back(text);
            }
            Phase::Stopped { .. } | Phase::Quitting { .. } => {
                self.input.set(&text);
            }
        }
        self.dirty = true;
    }

    /// Yougori's own slash commands (OWN_COMMANDS), handled here and never sent to the engine:
    /// whether `text` was one.
    fn own_command(&mut self, text: &str, now: Instant) -> bool {
        let Some(rest) = text.trim().strip_prefix('/') else { return false };
        let (name, argument) = match rest.split_once(char::is_whitespace) {
            Some((name, argument)) => (name, argument.trim()),
            None => (rest, ""),
        };
        match name.to_ascii_lowercase().as_str() {
            "harness" => self.open_harness(Some(argument), now),
            "model" if argument.is_empty() => self.open_models(now),
            "model" => match self.models.iter().find(|model| model.eq_ignore_ascii_case(argument)).cloned() {
                Some(model) => self.choose_model(model, now),
                None => self.transcript.notice(Level::Warn, &format!("There is no model {argument} here. /model lists them.")),
            },
            "new" => self.new_chat(now),
            "history" => self.effects.push(Effect::History),
            "help" => self.overlay = Some(Overlay::Keys { keys: keys_help(self.shift_enter), scroll: 0 }),
            "exit" | "quit" => self.request_quit(),
            _ => return false,
        }
        true
    }

    /// Saves the chat on the person's computer when something was said in it, with the session's
    /// secrets masked as the screen masks them.
    pub fn save_chat(&mut self) {
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
        self.effects.push(Effect::Save(Box::new(chat)));
    }

    /// A save's outcome: whether the person's computer keeps the chats. A failure is said once.
    pub fn saved(&mut self, outcome: Result<bool, String>) {
        match outcome {
            Ok(kept) => self.kept = Some(kept),
            Err(error) if !self.save_failed => {
                self.save_failed = true;
                self.transcript.notice(Level::Warn, &format!("This chat could not be saved: {}", clean(&error).trim()));
                self.dirty = true;
            }
            Err(_) => {}
        }
    }

    /// Whether a chat can be started or opened now: not while a turn runs or the engine starts.
    fn chat_free(&mut self, what: &str) -> bool {
        if self.resuming {
            self.transcript.notice(Level::Info, &format!("Wait until the chat this session goes on with has opened, then {what}."));
            return false;
        }
        if self.busy() || self.waiting.is_some() {
            self.transcript.notice(Level::Info, &format!("Stop the turn first (Esc), then {what}."));
            return false;
        }
        if !matches!(self.phase, Phase::Ready | Phase::Stopped { .. }) {
            self.transcript.notice(Level::Info, &format!("Wait until {} has started, then {what}.", self.name));
            return false;
        }
        true
    }

    /// The engine forgets the chat it had: its own new session when it offers one, else it starts
    /// again. Nothing when it had none of it.
    fn engine_forgets(&mut self, had: bool, now: Instant) {
        if !had || self.stopped() {
            return;
        }
        if self.offers("new") {
            self.quiet_new = true;
            self.turn = Some(Turn { started: now, stopping: false, prompt: "/new".into(), replaying: false });
            self.deliver("/new".into(), None);
        } else if self.owns_engine {
            // Its next Ready is the fresh engine's.
            self.quiet_new = true;
            self.effects.push(Effect::Renew);
        }
    }

    /// /new: the chat is kept, and a new one starts on a fresh screen.
    pub fn new_chat(&mut self, now: Instant) {
        if !self.chat_free("start a new chat") {
            return;
        }
        self.save_chat();
        let had = self.given > 0 || self.screen_given;
        self.chat = Chat::new(&self.spec.harness, &self.name, &self.model);
        self.forget_shown();
        self.conversed = false;
        self.engine_forgets(had, now);
        self.dirty = true;
    }

    /// The conversation shown, the plan, the findings and the counts go (a new or opened chat).
    fn forget_shown(&mut self) {
        self.given = 0;
        self.screen_given = false;
        self.chat_message = None;
        self.chat_tools.clear();
        self.transcript.clear();
        self.plan.clear();
        self.findings = [0; 5];
        self.usage = Usage::default();
        self.scroll = Scroll::Follow;
    }

    /// /history's list came: its panel opens (or what went wrong is said).
    pub fn listed(&mut self, listing: Result<Listing, String>) {
        self.dirty = true;
        let listing = match listing {
            Ok(listing) => listing,
            Err(error) => {
                self.transcript.notice(Level::Warn, &format!("Could not read the chats: {}", clean(&error).trim()));
                return;
            }
        };
        self.kept = Some(listing.kept);
        if listing.chats.is_empty() {
            self.transcript.notice(Level::Info, "No chats yet. Each chat is kept here once something is said in it.");
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

    /// A chat chosen in /history.
    fn pick_chat(&mut self, id: String) {
        self.overlay = None;
        if id == self.chat.id {
            return;
        }
        if self.chat_free("open another chat") {
            self.effects.push(Effect::Open(id));
        }
    }

    /// The chat opened from /history came: it is shown, and goes on here; the harness gets it with
    /// the next prompt.
    pub fn opened(&mut self, chat: Result<Chat, String>, now: Instant) {
        self.dirty = true;
        let mut chat = match chat {
            Ok(chat) => chat,
            Err(error) => {
                self.transcript.notice(Level::Warn, &format!("Could not open the chat: {}", clean(&error).trim()));
                return;
            }
        };
        if chat.id == self.chat.id || !self.chat_free("open another chat") {
            return;
        }
        self.save_chat();
        let had = self.given > 0 || self.screen_given;
        chat.entered(&self.spec.harness, &self.name, &self.model);
        self.chat = chat;
        self.forget_shown();
        self.show_chat();
        self.engine_forgets(had, now);
    }

    /// The chat this session goes on with came (a /harness switch into a new VM brought it),
    /// whether the engine has started yet or not: it is shown and goes on here, and the engine gets
    /// it with the first prompt, which waited for it.
    pub fn resumed(&mut self, chat: Result<Chat, String>, now: Instant) {
        self.resuming = false;
        self.dirty = true;
        match chat {
            Ok(mut chat) => {
                chat.entered(&self.spec.harness, &self.name, &self.model);
                self.chat = chat;
                self.given = 0;
                self.screen_given = false;
                self.chat_message = None;
                self.chat_tools.clear();
                self.show_chat();
            }
            Err(error) => {
                self.transcript.notice(Level::Warn, &format!("Could not open the chat to go on with: {}", clean(&error).trim()));
            }
        }
        self.send_queued(now);
    }

    /// The chat's turns on the screen, as they were said, under a line naming it.
    fn show_chat(&mut self) {
        let at = history::now();
        let from = if self.chat.harnesses.is_empty() { self.chat.name.clone() } else { self.chat.harnesses.join(" → ") };
        self.transcript.divider(&format!("{} · {} · {}", self.chat.title, from, history::ago(self.chat.updated, at)));
        if self.chat.turns.is_empty() && !self.chat.screen.trim().is_empty() {
            let lines: Vec<&str> = self.chat.screen.lines().collect();
            let tail = lines[lines.len().saturating_sub(40)..].join("\n");
            self.transcript.notice(Level::Info, &format!("What it showed, at the end:\n{tail}"));
        }
        for (i, turn) in self.chat.turns.clone().iter().enumerate() {
            match turn.role {
                history::Role::User => self.transcript.user(&turn.text),
                history::Role::Assistant => {
                    if !turn.tools.is_empty() {
                        self.transcript.notice(Level::Info, &format!("{} ran: {}", turn.harness, turn.tools.join(" · ")));
                    }
                    if !turn.text.trim().is_empty() {
                        self.transcript.message(&format!("history-{i}"), &turn.text, None);
                    }
                }
            }
        }
        self.transcript.end_turn();
        self.transcript.divider(&format!("{} goes on with this chat from here", self.name));
        self.conversed = true;
    }

    /// Opens the /harness panel, or goes straight to `named` (its questions, or the switch when it
    /// asks none). Not while the engine starts or switches: wait for it.
    pub fn open_harness(&mut self, named: Option<&str>, now: Instant) {
        self.overlay = None;
        if !matches!(self.phase, Phase::Ready | Phase::Stopped { .. }) {
            self.transcript.notice(Level::Info, &format!("Wait until {} has started to switch harness.", self.name));
            return;
        }
        match Flow::open(&self.spec, &self.switch_model(), named.filter(|named| !named.trim().is_empty())) {
            Ok((_, FlowOutcome::Start(pending))) => self.start_switch(*pending, now),
            Ok((flow, _)) => self.overlay = Some(Overlay::Switch(Box::new(flow))),
            Err(error) => self.transcript.notice(Level::Warn, &error),
        }
    }

    /// The switch starts: in place the engine stops for it (its turn and open requests end with
    /// it), a new VM's switch leaves it running until the launcher takes over. Secrets typed for
    /// the next harness stay in this process's memory, for its engine and the masking.
    pub fn start_switch(&mut self, pending: Pending, now: Instant) {
        self.overlay = None;
        for (id, value) in &pending.secrets {
            crate::spec::remember_secret(id, value);
        }
        if pending.mode == Where::InPlace {
            if self.turn.take().is_some() || self.transcript.live() {
                self.transcript.end_turn();
            }
            self.dialogs.clear();
            self.change_from = None;
        } else {
            self.change_from = Some(self.phase.clone());
        }
        let step = if pending.mode == Where::InPlace { format!("stopping {}", self.name) } else { "asking for a new VM".into() };
        self.phase = Phase::Changing { name: pending.entry.name.clone(), since: now, step };
        self.change = Some(Box::new(pending.clone()));
        self.effects.push(Effect::Switch(Box::new(pending)));
        self.dirty = true;
    }

    /// The switch's latest progress.
    pub fn change_step(&mut self, step: &str) {
        if let Phase::Changing { step: current, .. } = &mut self.phase {
            *current = step.to_owned();
        }
        self.dirty = true;
    }

    /// The switch failed: said in the conversation. A new VM's switch goes back to how it was (the
    /// engine kept running); in place, the run loop starts the harness before again, or Yougori's
    /// own agent when the harness before is gone. Whether it was in place.
    pub fn change_failed(&mut self, reason: &str, now: Instant) -> bool {
        let pending = self.change.take();
        let name = pending.as_ref().map_or_else(|| "the harness".to_owned(), |p| p.entry.name.clone());
        self.transcript.notice(Level::Error, &format!("Could not switch to {name}: {}", clean(reason).trim()));
        let in_place = pending.as_ref().is_none_or(|p| p.mode == Where::InPlace);
        if in_place {
            self.phase = Phase::Starting { since: now };
        } else if matches!(self.phase, Phase::Changing { .. }) {
            self.phase = self.change_from.take().unwrap_or(Phase::Ready);
            self.send_queued(now);
        }
        self.dirty = true;
        in_place
    }

    /// What the next screen keeps after a switch to `next`: the conversation so far, the prompts
    /// sent and those still queued, under a divider naming the next harness and model.
    pub fn carry(&mut self, next: &EngineSpec) -> super::Carry {
        super::Carry {
            transcript: Some(std::mem::take(&mut self.transcript)),
            history: std::mem::take(&mut self.history),
            queued: std::mem::take(&mut self.queued),
            note: format!("{} · {}", next.name, crate::spec::shown_model(&next.model)),
            chat: Some(std::mem::take(&mut self.chat)),
        }
    }

    /// What the screen before handed on (a switch): its conversation goes on here, and this
    /// harness takes the chat over with the next prompt.
    pub fn carried(&mut self, carry: super::Carry) {
        if let Some(transcript) = carry.transcript {
            self.transcript = transcript;
            self.conversed = true;
        }
        self.history = carry.history;
        self.queued = carry.queued;
        if !carry.note.is_empty() {
            self.transcript.divider(&carry.note);
        }
        if let Some(mut chat) = carry.chat {
            chat.entered(&self.spec.harness, &self.spec.name, &self.spec.model);
            if !chat.is_empty() {
                self.transcript.notice(Level::Info, &format!("{} takes this chat over with your next message.", self.spec.name));
            }
            self.chat = chat;
            self.given = 0;
            self.screen_given = false;
        }
    }

    /// Esc while work runs: asks the engine to stop the turn.
    pub fn cancel(&mut self) -> bool {
        // Only a turn stops: work the engine does on its own (starting its servers) goes on.
        if self.turn.is_none() || self.phase != Phase::Ready {
            return false;
        }
        if let Some(waiting) = self.waiting.take() {
            // The prompt never reached the engine: it leaves the chat and goes back into the box.
            if self.chat.turns.last().is_some_and(|turn| turn.role == history::Role::User) {
                self.chat.turns.pop();
            }
            self.turn = None;
            self.transcript.end_turn();
            self.transcript.notice(Level::Info, "Stopped before the earlier chat was compacted.");
            if self.input.is_empty() {
                self.input.set(&waiting.prompt);
            }
            self.unqueue();
            self.dirty = true;
            return true;
        }
        if let Some(turn) = self.turn.as_mut() {
            turn.stopping = true;
        }
        self.send(Command::Cancel);
        self.unqueue();
        self.dirty = true;
        true
    }

    /// Quit, asking first while a turn runs.
    pub fn request_quit(&mut self) {
        if self.turn.is_some() && self.phase == Phase::Ready {
            self.overlay = Some(Overlay::Confirm {
                what: Confirmable::Quit,
                title: format!("Quit while {} is working?", self.name),
                detail: format!("The running turn stops and {} ends.", self.name),
                yes: "quit".into(),
            });
        } else {
            self.quit();
        }
    }

    pub fn quit(&mut self) {
        self.overlay = None;
        if !matches!(self.phase, Phase::Quitting { .. }) {
            self.effects.push(Effect::Quit);
        }
    }

    /// The command palette's entries for the engine's state.
    pub fn palette(&self) -> Vec<Item> {
        let not_offered = format!("not offered by {}", self.name);
        let slash = |name: &str, label: &str| Item {
            action: Action::Slash(name.into()),
            label: label.into(),
            hint: if self.offers(name) { format!("/{name}") } else { not_offered.clone() },
            enabled: self.offers(name) && self.phase == Phase::Ready,
        };
        let mode_name = self.mode_name().unwrap_or_default();
        let switchable = self.spec.session.switchable();
        let mut items = vec![
            Item {
                action: Action::Harness,
                label: "Switch harness".into(),
                hint: if switchable { format!("{} · /harness", self.name) } else { "needs a newer Yougori".into() },
                // Not while the engine starts or switches: wait for it.
                enabled: switchable && matches!(self.phase, Phase::Ready | Phase::Stopped { .. }),
            },
            Item {
                action: Action::Model,
                label: "Switch model".into(),
                hint: self.model.clone(),
                // Not while the engine starts or switches: wait for it.
                enabled: matches!(self.phase, Phase::Ready | Phase::Stopped { .. }),
            },
            Item {
                action: Action::Mode,
                label: "Switch mode".into(),
                hint: if self.modes.is_empty() { not_offered.clone() } else { mode_name },
                enabled: !self.modes.is_empty() && self.phase == Phase::Ready,
            },
            Item {
                action: Action::NewChat,
                label: "New chat".into(),
                hint: "this one is kept · /new".into(),
                enabled: matches!(self.phase, Phase::Ready | Phase::Stopped { .. }) && !self.busy(),
            },
            Item { action: Action::History, label: "History".into(), hint: "earlier chats · /history".into(), enabled: true },
            slash("sessions", "Sessions"),
            slash("compact", "Compact"),
            Item {
                action: Action::Details,
                label: if self.details { "Hide details".into() } else { "Show details".into() },
                hint: "thinking and full output · ctrl+o".into(),
                enabled: true,
            },
            Item { action: Action::Keys, label: "Keys".into(), hint: "every shortcut".into(), enabled: true },
        ];
        if self.stopped() && self.owns_engine {
            items.insert(
                0,
                Item {
                    action: Action::Restart,
                    label: "Start again".into(),
                    hint: format!("start {}", self.name),
                    enabled: true,
                },
            );
        }
        items.push(Item { action: Action::Quit, label: "Quit".into(), hint: "ctrl+c twice".into(), enabled: true });
        items
    }

    pub fn mode_name(&self) -> Option<String> {
        let current = self.mode.as_ref()?;
        Some(self.modes.iter().find(|m| &m.id == current).map(|m| m.name.clone()).unwrap_or_else(|| current.clone()))
    }

    pub fn model_picker(&self) -> Overlay {
        let choices = self
            .models
            .iter()
            .map(|m| Choice {
                id: m.clone(),
                label: crate::spec::shown_model(m),
                hint: match crate::spec::plan_of(m) {
                    Some(_) => "your own subscription".into(),
                    None => String::new(),
                },
                current: *m == self.model,
            })
            .collect();
        Overlay::picker(Which::Models, "Switch model", choices)
    }

    /// How to sign in to a plan the person chose: the sign-in already in this VM, this computer's
    /// own client's, the one Yougori kept, or signing in now. A plan whose sign-in may not travel
    /// (Claude: spec::Plan::transferable) offers only the client's own sign-in in this VM. The
    /// title says what the VM reaches.
    pub fn plan_picker(&self, plan: &crate::spec::Plan, model: &str) -> Overlay {
        let home = self.spec.env.get("HOME").map(String::as_str).unwrap_or("/home/node");
        let here = std::path::Path::new(home).join(plan.credential).metadata().is_ok_and(|m| m.len() > 2);
        let held = if plan.transferable {
            self.spec.session.subscriptions.get(plan.id).cloned().unwrap_or_default()
        } else {
            crate::spec::PlanState::default()
        };
        let client = &self.name;
        let mut choices = Vec::new();
        if here {
            choices.push(Choice { id: "vm".into(), label: "Use the sign-in in this VM".into(), hint: String::new(), current: true });
        }
        if held.computer {
            choices.push(Choice {
                id: "computer".into(),
                label: "Use this computer's sign-in".into(),
                hint: format!("from your own {client}; a renewed one comes back"),
                current: choices.is_empty(),
            });
        }
        if held.saved {
            choices.push(Choice {
                id: "saved".into(),
                label: "Use the sign-in Yougori kept".into(),
                hint: "from an earlier session".into(),
                current: choices.is_empty(),
            });
        }
        choices.push(Choice {
            id: "sign-in".into(),
            label: "Sign in".into(),
            hint: plan.how.into(),
            current: choices.is_empty(),
        });
        let title = format!("Your {} plan · the VM reaches {} only", plan.name, plan.sites.join(", "));
        Overlay::picker(Which::Plan(model.to_owned()), &title, choices)
    }

    /// The person chose how to sign in to the plan of `model`: the launcher sets it up, then the
    /// engine restarts with it.
    fn pick_plan(&mut self, model: String, using: String, now: Instant) {
        self.overlay = None;
        let Some((plan, _)) = crate::spec::plan_of(&model) else { return };
        if !matches!(self.phase, Phase::Ready | Phase::Stopped { .. }) {
            self.transcript.notice(Level::Info, &format!("Wait until {} has started to switch models.", self.name));
            return;
        }
        self.switch_from = Some(self.phase.clone());
        self.phase = Phase::Switching { model: model.clone(), since: now, step: format!("opening {}", plan.sites.join(", ")) };
        self.effects.push(Effect::Subscribe { plan: plan.id.into(), using, model });
        self.dirty = true;
    }

    /// The plan is set up (its sites open, its sign-in in place): the engine restarts with it.
    pub fn subscribed(&mut self, model: String, outcome: Result<(), String>, now: Instant) {
        match outcome {
            Ok(()) => {
                self.switch_step("Preparing");
                self.effects.push(Effect::Restart(model));
            }
            Err(error) => self.switch_refused(&model, &error, now),
        }
    }

    /// The model a harness switched to starts with: the one in use, or this inference's first when
    /// the one in use is a plan of this client's own.
    pub fn switch_model(&self) -> String {
        if !crate::spec::subscribed(&self.model) {
            return self.model.clone();
        }
        self.models.iter().find(|model| !crate::spec::subscribed(model)).cloned().unwrap_or_else(|| self.model.clone())
    }

    pub fn mode_picker(&self) -> Overlay {
        let choices = self
            .modes
            .iter()
            .map(|m| Choice {
                id: m.id.clone(),
                label: m.name.clone(),
                hint: m.description.clone().unwrap_or_default(),
                current: self.mode.as_deref() == Some(m.id.as_str()),
            })
            .collect();
        Overlay::picker(Which::Modes, "Switch mode", choices)
    }

    /// Switches to the next (or previous) mode the engine offers.
    pub fn cycle_mode(&mut self, back: bool) {
        if self.modes.is_empty() || self.phase != Phase::Ready {
            return;
        }
        let at = self.mode.as_ref().and_then(|m| self.modes.iter().position(|x| &x.id == m)).unwrap_or(0);
        let count = self.modes.len();
        let next = if back { (at + count - 1) % count } else { (at + 1) % count };
        let id = self.modes[next].id.clone();
        self.set_mode(id);
    }

    fn set_mode(&mut self, id: String) {
        self.mode = Some(id.clone());
        self.send(Command::SetMode { mode: id });
    }

    /// The person chose a model.
    pub fn choose_model(&mut self, model: String, now: Instant) {
        self.overlay = None;
        if !matches!(self.phase, Phase::Ready | Phase::Stopped { .. }) {
            self.transcript.notice(Level::Info, &format!("Wait until {} has started to switch models.", self.name));
            return;
        }
        if model == self.model && !self.stopped() {
            return;
        }
        // A plan the person signs in to (their own subscription), which this client does not run
        // on yet: how to sign in is asked first.
        if let Some((plan, _)) = crate::spec::plan_of(&model) {
            if plan.harness != self.spec.harness {
                let client = self.spec.session.harnesses.iter().find(|h| h.id == plan.harness).map_or(plan.client, |h| h.name.as_str());
                self.transcript.notice(
                    Level::Info,
                    &format!("Your {} plan works in {client}: /harness {} first.", plan.name, plan.harness),
                );
                return;
            }
            let on_it = crate::spec::plan_of(&self.model).is_some_and(|(current, _)| current.id == plan.id);
            if !on_it {
                if !self.owns_engine {
                    self.transcript.notice(Level::Warn, &format!("{} cannot be started again from here.", self.name));
                } else {
                    self.overlay = Some(self.plan_picker(plan, &model));
                }
                return;
            }
        }
        // Nothing runs yet (no model): the harness starts on the one chosen.
        if self.model.is_empty() && self.owns_engine {
            self.switch_by_restart(model, now);
            return;
        }
        if self.stopped() {
            if self.owns_engine {
                self.switch_by_restart(model, now);
            } else {
                self.transcript.notice(Level::Warn, &format!("{} cannot be started again from here.", self.name));
            }
            return;
        }
        // A model the inference added after the engine started is not in its settings: it starts
        // again, set up with the inference's models as they are now.
        let live = self.model_switch == ModelSwitch::Live && (self.engine_models.contains(&model) || !self.owns_engine);
        match self.model_switch {
            // The engine answers with a Model event, which says "Now using …".
            _ if live => self.send(Command::SetModel { model }),
            ModelSwitch::Live | ModelSwitch::Restart if self.owns_engine => {
                let shown = crate::spec::shown_model(&model);
                self.overlay = Some(Overlay::Confirm {
                    detail: if self.chat.is_empty() {
                        format!("Switching to {shown} restarts {}.", self.name)
                    } else {
                        format!("Switching to {shown} restarts {}; it gets this chat with your next message.", self.name)
                    },
                    title: format!("Switch to {shown}?"),
                    what: Confirmable::SwitchModel(model),
                    yes: "switch".into(),
                });
            }
            ModelSwitch::Live | ModelSwitch::Restart => {
                self.transcript.notice(
                    Level::Warn,
                    &format!("{} switches models by restarting, which this screen cannot do here.", self.name),
                );
            }
        }
    }

    /// /model: the picker, with this inference's models read afresh (they may have changed since
    /// the session started: the person's own APIs, the inference's models, the plans). With none
    /// listed yet it opens once they have been read.
    pub fn open_models(&mut self, now: Instant) {
        self.read_models(now);
        if self.models.is_empty() {
            self.models_wanted = true;
        } else {
            self.overlay = Some(self.model_picker());
        }
    }

    fn read_models(&mut self, now: Instant) {
        if !self.models_reading {
            self.models_reading = true;
            self.models_read = Some(now);
            self.effects.push(Effect::Models);
        }
    }

    /// While the session has no model yet, the inference's models are read again now and then,
    /// and the person is told when one comes.
    pub fn poll_models(&mut self, now: Instant) {
        let due = self.models_read.is_none_or(|at| now.saturating_duration_since(at) >= MODELS_AGAIN);
        if self.model.is_empty() && self.phase == Phase::Ready && self.owns_engine && due {
            self.read_models(now);
        }
    }

    /// This inference's models, read afresh (or why not): /model lists them, then the plans of
    /// this harness's client, with the model in use kept. An open model picker shows them.
    pub fn models_listed(&mut self, listed: Result<Vec<String>, String>) {
        self.models_reading = false;
        self.dirty = true;
        let wanted = std::mem::take(&mut self.models_wanted);
        let live = match listed {
            Ok(live) => live,
            Err(error) => {
                if wanted {
                    self.transcript.notice(Level::Warn, &format!("Could not read this inference's models: {}", clean(&error).trim()));
                }
                return;
            }
        };
        let mut next: Vec<String> = Vec::new();
        for model in live {
            if !model.trim().is_empty() && !crate::spec::subscribed(&model) && !next.contains(&model) {
                next.push(model);
            }
        }
        for model in self.spec.models.iter().chain(self.models.iter()) {
            if crate::spec::subscribed(model) && !next.contains(model) {
                next.push(model.clone());
            }
        }
        if !self.model.is_empty() && !next.contains(&self.model) {
            next.insert(0, self.model.clone());
        }
        let added: Vec<String> =
            next.iter().filter(|model| !self.models.contains(model)).map(|model| crate::spec::shown_model(model)).collect();
        self.models = next;
        let picking = matches!(&self.overlay, Some(Overlay::Picker { which: Which::Models, .. }));
        if wanted || picking {
            if self.models.is_empty() {
                self.overlay = None;
                self.transcript.notice(Level::Info, &self.no_model_words());
                return;
            }
            // The cursor stays on the model it was on.
            let on = match &self.overlay {
                Some(Overlay::Picker { which: Which::Models, choices, selected, .. }) => choices.get(*selected).map(|c| c.id.clone()),
                _ => None,
            };
            let mut picker = self.model_picker();
            if let (Some(on), Overlay::Picker { choices, selected, .. }) = (on, &mut picker) {
                if let Some(at) = choices.iter().position(|choice| choice.id == on) {
                    *selected = at;
                }
            }
            self.overlay = Some(picker);
        } else if self.model.is_empty() && !added.is_empty() {
            self.transcript.notice(Level::Info, &format!("{} can be used now: choose it with /model.", added.join(", ")));
        }
    }

    /// What a session with no model yet says: what starts once one is chosen, and where models
    /// come from when the inference lists none for the person.
    pub fn no_model_words(&self) -> String {
        if self.models.iter().any(|model| !crate::spec::subscribed(model)) {
            return format!("{} starts once you choose a model: /model lists them.", self.name);
        }
        let mut plans: Vec<&str> = Vec::new();
        for (plan, _) in self.models.iter().filter_map(|model| crate::spec::plan_of(model)) {
            if !plans.contains(&plan.name) {
                plans.push(plan.name);
            }
        }
        let mut words = format!(
            "{} starts once you choose a model. This inference has no models for you yet: add your own API under \
             Your APIs on its website, or ask its administrator to connect models in Admin.",
            self.name
        );
        if !plans.is_empty() {
            words.push_str(&format!(" Or use your own {} plan.", plans.join(" or ")));
        }
        words.push_str(" /model lists them as they come.");
        words
    }

    /// Starts a switch by restarting. The running turn and its open requests stay with the engine
    /// that runs until it is stopped for the new one ([`App::switch_prepared`]); when the new model
    /// cannot be prepared they go on as before ([`App::switch_refused`]).
    fn switch_by_restart(&mut self, model: String, now: Instant) {
        self.switch_from = Some(self.phase.clone());
        self.phase = Phase::Switching { model: model.clone(), since: now, step: "Preparing".into() };
        self.effects.push(Effect::Restart(model));
    }

    pub fn outcome(&mut self, outcome: Outcome, now: Instant) {
        self.dirty = true;
        match outcome {
            Outcome::Stay => {}
            Outcome::Close => self.overlay = None,
            Outcome::Run(action) => {
                self.overlay = None;
                match action {
                    Action::Harness => self.open_harness(None, now),
                    Action::Model => self.open_models(now),
                    Action::Mode => self.overlay = Some(self.mode_picker()),
                    Action::Slash(name) => {
                        let text = format!("/{name}");
                        if self.turn.is_none() && self.phase == Phase::Ready {
                            self.history.push(&text);
                            self.send_prompt(text, now);
                        } else {
                            self.queued.push_back(text);
                        }
                    }
                    Action::Details => self.details = !self.details,
                    Action::Keys => self.overlay = Some(Overlay::Keys { keys: keys_help(self.shift_enter), scroll: 0 }),
                    Action::Quit => self.request_quit(),
                    Action::Restart => self.effects.push(Effect::Start),
                    Action::NewChat => self.new_chat(now),
                    Action::History => self.effects.push(Effect::History),
                    Action::SendCtrlP => {}
                }
            }
            Outcome::Pick(Which::Chats, id) => self.pick_chat(id),
            Outcome::Pick(Which::Plan(model), using) => self.pick_plan(model, using, now),
            Outcome::Pick(Which::Models, model) => self.choose_model(model, now),
            Outcome::Pick(Which::Modes, mode) => {
                self.overlay = None;
                if self.mode.as_deref() != Some(mode.as_str()) {
                    self.set_mode(mode);
                }
            }
            Outcome::Confirmed(Confirmable::Quit) => self.quit(),
            Outcome::Confirmed(Confirmable::SwitchModel(model)) => {
                self.overlay = None;
                self.switch_by_restart(model, now);
            }
            Outcome::Switch(pending) => self.start_switch(*pending, now),
            // A terminal pane's own (its command line, /new): the chat never opens them.
            Outcome::Command(..) | Outcome::Pass(_) | Outcome::Confirmed(Confirmable::Renew) => self.overlay = None,
        }
    }

    /// The slash commands matching what is typed, while a command name is being typed: Yougori's
    /// own first, then the engine's (one with the name of Yougori's is Yougori's).
    pub fn completions(&self) -> Vec<&SlashCommand> {
        let text = self.input.text();
        if !text.starts_with('/')
            || text.contains(char::is_whitespace)
            || self.completion_closed.as_deref() == Some(text)
        {
            return Vec::new();
        }
        let typed = text[1..].to_lowercase();
        let engine = self.commands.iter().filter(|c| !self.own.iter().any(|own| own.name == c.name));
        let all: Vec<&SlashCommand> = self.own.iter().chain(engine).collect();
        let mut starts: Vec<&SlashCommand> =
            all.iter().copied().filter(|c| c.name.to_lowercase().starts_with(&typed)).collect();
        let contains = all
            .iter()
            .copied()
            .filter(|c| !c.name.to_lowercase().starts_with(&typed) && c.name.to_lowercase().contains(&typed));
        starts.extend(contains);
        starts
    }

    // Restart progress, reported by the run loop.

    pub fn switch_step(&mut self, step: &str) {
        if let Phase::Switching { step: current, .. } = &mut self.phase {
            *current = step.to_owned();
        }
        self.dirty = true;
    }

    /// The new engine started with the new model; Ready follows from it.
    pub fn switched(&mut self, spec: EngineSpec, model: &str, now: Instant) {
        // Nothing ran before (no model yet): the harness has just started.
        let first = self.model.is_empty();
        self.name = spec.name.clone();
        self.model = model.to_owned();
        self.spec = spec;
        self.session = None;
        self.usage = Usage::default();
        self.plan.clear();
        // The new engine has none of the chat: it gets it with the next prompt.
        self.given = 0;
        self.screen_given = false;
        self.chat.model = model.to_owned();
        let shown = crate::spec::shown_model(model);
        if first {
            self.transcript.divider(&format!("{} started with {shown}.", self.name));
        } else if self.chat.is_empty() {
            self.transcript.divider(&format!("{} restarted with {shown}. The conversation starts again.", self.name));
        } else {
            self.transcript.divider(&format!("{} restarted with {shown}. It gets this chat with your next message.", self.name));
        }
        self.phase = Phase::Starting { since: now };
        self.dirty = true;
    }

    /// The new model is prepared and the engine that runs is being stopped for it: its turn and
    /// open requests end with it.
    pub fn switch_prepared(&mut self) {
        self.switch_from = None;
        if self.turn.take().is_some() || self.transcript.live() {
            self.transcript.end_turn();
        }
        self.dialogs.clear();
        self.switch_step(&format!("stopping {}", self.name));
    }

    /// The new model could not be prepared. Nothing was stopped: the screen goes back to how it
    /// was (an engine that runs keeps its turn and open requests; a stopped one stays stopped),
    /// and prompts typed meanwhile go when the engine can take them.
    pub fn switch_refused(&mut self, model: &str, error: &str, now: Instant) {
        self.transcript.notice(Level::Error, &format!("Could not switch to {}: {}", crate::spec::shown_model(model), clean(error).trim()));
        let before = self.switch_from.take();
        // The engine may have stopped meanwhile (its Exit says so): that stays.
        if matches!(self.phase, Phase::Switching { .. }) {
            self.phase = before.unwrap_or(if self.ready_once { Phase::Ready } else { Phase::Starting { since: now } });
        }
        self.send_queued(now);
        self.dirty = true;
    }

    /// The new engine did not start; the old one was started again, fresh: it gets the chat with
    /// the next prompt.
    pub fn switch_reverted(&mut self, model: &str, error: &str, now: Instant) {
        use crate::spec::shown_model;
        self.transcript.notice(
            Level::Error,
            &format!("Could not start {} with {}: {}. Back on {}.", self.name, shown_model(model), clean(error).trim(), shown_model(&self.model)),
        );
        self.starting(now);
    }

    /// Neither the new model nor the old one started.
    pub fn switch_failed(&mut self, model: &str, error: &str, again: &str) {
        use crate::spec::shown_model;
        let message = format!(
            "With {}: {}. With {} again: {}. Press Enter to try again, or Ctrl+P to choose another model.",
            shown_model(model),
            clean(error).trim(),
            shown_model(&self.model),
            clean(again).trim()
        );
        self.stop(format!("{} could not start", self.name), message, true);
    }
}

/// Every key, for the keys help. A terminal that does not tell Shift+Enter from Enter sends
/// the prompt on Shift+Enter, so Ctrl+J is named for a new line there.
pub fn keys_help(shift_enter: bool) -> Vec<(String, String)> {
    let new_line = if shift_enter {
        ("shift+enter", "new line (also alt+enter, ctrl+j)")
    } else {
        ("ctrl+j", "new line (also alt+enter)")
    };
    [
        ("enter", "send (while it works: queue for after)"),
        new_line,
        ("↑ ↓", "earlier prompts at the first or last line"),
        ("esc", "stop the turn"),
        ("ctrl+c", "clear the box or stop; twice to quit"),
        ("ctrl+p", "commands: harness, model, mode, sessions, keys"),
        ("tab", "next mode (when the box is empty)"),
        ("/", "commands: Yougori's, then the harness's own"),
        ("/harness", "switch to another harness (/harness NAME)"),
        ("/model", "switch model (/model ID)"),
        ("/new", "start a new chat; this one is kept"),
        ("/history", "open an earlier chat, from any harness"),
        ("/help", "these keys and commands"),
        ("/exit", "end the session"),
        ("pgup pgdn", "scroll the conversation"),
        ("end", "follow the newest output again"),
        ("ctrl+o", "show or hide thinking and full output"),
        ("ctrl+l", "redraw the screen"),
        ("ctrl+a ctrl+e", "start or end of the line"),
        ("ctrl+u ctrl+k", "delete to the start or end of the line"),
        ("ctrl+w", "delete the word before"),
        ("alt+← alt+→", "move by word"),
    ]
    .iter()
    .map(|(k, v)| (k.to_string(), v.to_string()))
    .collect()
}
