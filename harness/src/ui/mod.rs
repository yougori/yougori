//! The full-screen interface: one Yougori screen for whichever harness the person chose.
//!
//! Engines that speak the event model (`protocol.rs`) get the conversation screen (`state.rs`
//! for what it holds, `keys.rs`, `draw.rs`); a harness run as its own terminal client
//! (`Protocol::Terminal`) gets the terminal pane (`pane.rs`). Both redraw only when something
//! changed, at most about 30 times a second, and write only what changed (`screen.rs`), because
//! the screen reaches the person through the launcher's polling relay.
mod dialog;
mod draw;
mod highlight;
mod input;
mod keys;
mod markdown;
mod mask;
mod overlay;
mod pane;
mod screen;
mod state;
mod switcher;
mod term;
#[cfg(test)]
mod tests;
mod text;
mod theme;
mod transcript;

use crate::history::{self, Chat, Keeper, Listing, Saver};
use crate::protocol::{Command, EngineHandle, Event, Level};
use crate::spec::{EngineSpec, Protocol};
use std::sync::Arc;
use crossterm::event::{Event as TermEvent, EventStream};
use futures::future::{FutureExt, LocalBoxFuture};
use futures::stream::{FuturesUnordered, StreamExt};
use mask::Masker;
use pane::{Pane, PaneEffect, PanePhase};
use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use screen::Screen;
use state::{App, Effect, Phase};
use std::collections::VecDeque;
use std::time::{Duration, Instant};
use switcher::{Mode as Where, Pending, Switched, NEW_VM_EXIT, YOUGORI};
use term::Terminal;
use theme::{Depth, Theme};
use tokio::sync::{mpsc, oneshot};

/// The shortest time between two frames (about 30 a second).
const FRAME: Duration = Duration::from_millis(33);
/// How often spinners and clocks move.
const TICK: Duration = Duration::from_millis(80);
/// How long a quitting screen waits for the engine to stop by itself.
const STOP_WAIT: Duration = Duration::from_secs(3);
/// How long a model switch waits for the old engine to stop.
const SWITCH_WAIT: Duration = Duration::from_secs(10);
/// Engine events taken in one go before the screen looks at keys again.
const BATCH: usize = 512;

/// How a screen ended: the session is over (its exit code), or the person switched harness and
/// the next harness's screen takes over (/harness).
pub enum Ended {
    Exit(i32),
    Switch(Box<EngineSpec>, Box<Carry>),
    /// The next spec runs on a plan its client is not signed in to: the client's sign-in runs in
    /// the terminal pane first, then that spec; Esc there goes back to the one before (`back`).
    SignIn(Box<EngineSpec>, Box<EngineSpec>, Box<Carry>),
}

/// A client's sign-in to a plan, run in the terminal pane: the spec that starts once it signed in
/// (`then`), and the one Esc goes back to, when there is one.
pub struct SignIn {
    pub then: Box<EngineSpec>,
    pub back: Option<Box<EngineSpec>>,
}

/// The terminal pane's spec for a client's sign-in: the client's own sign-in command, in its
/// environment, with nothing to switch to from there.
fn sign_in_spec(then: &EngineSpec) -> EngineSpec {
    let mut spec = then.clone();
    let plan = crate::spec::plan_of(&then.model).map(|(plan, _)| plan.name).unwrap_or("your");
    spec.protocol = Protocol::Terminal;
    spec.argv = then.sign_in.clone();
    spec.sign_in = Vec::new();
    spec.services = Vec::new();
    spec.models = vec![then.model.clone()];
    spec.name = format!("{} · signing in to your {plan} plan", then.name);
    spec.session.harnesses = Vec::new();
    spec.session.switch = Vec::new();
    spec
}

/// What the next screen keeps of the one before after a switch: the conversation so far, the
/// prompts sent and queued, the divider it starts under ("OpenCode · gemma-4"), and the chat,
/// which the next harness takes over.
#[derive(Default)]
pub struct Carry {
    pub transcript: Option<transcript::Transcript>,
    pub history: input::History,
    pub queued: VecDeque<String>,
    pub note: String,
    pub chat: Option<Chat>,
}

/// Where the session's chats go (history.rs), shared by the screens of every harness it holds.
pub struct Books {
    pub keeper: Arc<Keeper>,
    pub saver: Saver,
    /// Each save's outcome.
    pub saved: mpsc::UnboundedReceiver<Result<bool, String>>,
}

impl Books {
    fn start() -> Books {
        let keeper = Arc::new(Keeper::from_env());
        let (saver, saved) = Saver::start(keeper.clone());
        Books { keeper, saver, saved }
    }
}

/// How long the screen waits for the chat to be saved before it ends or hands over to a new VM.
const SAVE_WAIT: Duration = Duration::from_secs(8);

/// Runs the screen until the person quits; returns the process exit code (0 after a normal
/// quit, 1 when the engine failed, [`NEW_VM_EXIT`] when the next harness starts in a new VM).
/// With `engine` None the screen starts the spec's engine itself (`crate::engines::start`), shows
/// its start-up and errors, restarts it to switch models (`crate::engines::respec`) and switches
/// harness (switcher.rs); a `Protocol::Terminal` harness runs in the terminal pane. A switch goes
/// on in the same terminal: the next harness's screen, conversation or pane, takes over.
pub async fn run(spec: EngineSpec, engine: Option<EngineHandle>) -> anyhow::Result<i32> {
    let theme = Theme::new(Depth::detect());
    highlight::warm();
    let mut terminal =
        Terminal::enter().map_err(|e| anyhow::anyhow!("The screen needs an interactive terminal: {e}"))?;
    let mut spec = spec;
    let mut engine = engine;
    let mut carry = Carry::default();
    let mut books = Books::start();
    // A chat the session goes on with (a /harness switch into a new VM): opened in the first screen.
    let mut resume = Some(spec.session.resume.clone()).filter(|id| history::valid_id(id));
    // A client's sign-in to a plan, to run before the spec it starts.
    let mut signing: Option<SignIn> = None;
    let result = loop {
        // Secrets typed for the harness switched to are masked from its first frame.
        let masker = Masker::from_environment();
        if signing.is_none() && !spec.sign_in.is_empty() && engine.is_none() {
            // The session starts on a plan its client is not signed in to: its sign-in first.
            signing = Some(SignIn { then: Box::new(spec.clone()), back: None });
        }
        let ended = if let Some(sign) = signing.take() {
            let shown = sign_in_spec(&sign.then);
            run_pane(&mut terminal, shown, theme, masker, std::mem::take(&mut carry), &mut books, None, Some(sign)).await
        } else if spec.protocol == Protocol::Terminal && engine.is_none() {
            run_pane(&mut terminal, spec, theme, masker, std::mem::take(&mut carry), &mut books, resume.take(), None).await
        } else {
            run_agent(&mut terminal, spec, engine.take(), theme, masker, std::mem::take(&mut carry), &mut books, resume.take())
                .await
        };
        match ended {
            Ok(Ended::Exit(code)) => break Ok(code),
            Ok(Ended::Switch(next, kept)) => {
                spec = *next;
                carry = *kept;
            }
            Ok(Ended::SignIn(next, back, kept)) => {
                spec = (*next).clone();
                signing = Some(SignIn { then: next, back: Some(back) });
                carry = *kept;
            }
            Err(error) => break Err(error),
        }
    };
    drop(terminal);
    result
}

/// The next progress line of the harness switch under way, if any.
async fn next_progress(progress: &mut Option<mpsc::UnboundedReceiver<String>>) -> Option<String> {
    match progress {
        Some(receiver) => receiver.recv().await,
        None => std::future::pending().await,
    }
}

/// What the terminal and the screen's timers do while the screen runs.
struct Io {
    screen: Screen,
    input: Option<EventStream>,
    detect: Option<tokio::sync::oneshot::Receiver<bool>>,
    last_frame: Option<Instant>,
}

impl Io {
    fn new() -> Io {
        let (width, height) = Terminal::size();
        // The keyboard question is asked before keys are read, so its answer is not taken for
        // typing; keys pressed meanwhile wait in the terminal's queue. It is asked on a thread of
        // its own, not the runtime's blocking pool, so a terminal that never answers cannot keep
        // the process from ending.
        let (answer, detect) = tokio::sync::oneshot::channel();
        let asked = std::thread::Builder::new()
            .name("yougori-keyboard".into())
            .spawn(move || {
                let _ = answer.send(Terminal::enhance_keyboard());
            })
            .is_ok();
        Io {
            screen: Screen::new(Rect::new(0, 0, width, height)),
            input: (!asked).then(EventStream::new),
            detect: asked.then_some(detect),
            last_frame: None,
        }
    }

    /// How long until a frame may be drawn.
    fn wait(&self, now: Instant) -> Duration {
        match self.last_frame {
            Some(at) => FRAME.saturating_sub(now.saturating_duration_since(at)),
            None => Duration::ZERO,
        }
    }

    fn resize(&mut self, width: u16, height: u16) {
        self.screen.resize(Rect::new(0, 0, width.max(1), height.max(1)));
    }
}

async fn next_input(input: &mut Option<EventStream>) -> Option<std::io::Result<TermEvent>> {
    match input {
        Some(stream) => stream.next().await,
        None => std::future::pending().await,
    }
}

async fn next_event(engine: &mut Option<EngineHandle>) -> Option<Event> {
    match engine {
        Some(handle) => handle.events.recv().await,
        None => std::future::pending().await,
    }
}

/// Whether the terminal reports Shift+Enter apart from Enter, once it answered.
async fn detected(detect: &mut Option<tokio::sync::oneshot::Receiver<bool>>) -> bool {
    match detect {
        Some(answer) => answer.await.unwrap_or(false),
        None => std::future::pending().await,
    }
}

#[cfg(unix)]
struct Signals {
    terminate: Option<tokio::signal::unix::Signal>,
    hangup: Option<tokio::signal::unix::Signal>,
}

#[cfg(unix)]
impl Signals {
    fn new() -> Signals {
        use tokio::signal::unix::{signal, SignalKind};
        Signals { terminate: signal(SignalKind::terminate()).ok(), hangup: signal(SignalKind::hangup()).ok() }
    }

    async fn next(&mut self) {
        let terminate = async {
            match self.terminate.as_mut() {
                Some(s) => {
                    s.recv().await;
                }
                None => std::future::pending().await,
            }
        };
        let hangup = async {
            match self.hangup.as_mut() {
                Some(s) => {
                    s.recv().await;
                }
                None => std::future::pending().await,
            }
        };
        tokio::select! {
            _ = terminate => {}
            _ = hangup => {}
        }
    }
}

#[cfg(not(unix))]
struct Signals;

#[cfg(not(unix))]
impl Signals {
    fn new() -> Signals {
        Signals
    }

    async fn next(&mut self) {
        std::future::pending::<()>().await
    }
}

/// Work the conversation screen waits for besides events and keys.
enum Job {
    Started(anyhow::Result<EngineHandle>),
    Respecced(String, anyhow::Result<EngineSpec>),
    OldStopped(String, EngineSpec),
    Switched(String, EngineSpec, anyhow::Result<EngineHandle>),
    Reverted(String, String, anyhow::Result<EngineHandle>),
    /// A harness switch ended (/harness).
    Changed(Switched),
    /// /history's list, a chat opened from it, the chat the session goes on with (a switch into a
    /// new VM brought it), and the model's notes on an earlier chat. These run beside the engine's
    /// own jobs (its start, a switch), which they never hold up.
    Listed(Result<Listing, String>),
    Opened(Result<Chat, String>),
    Resumed(Result<Chat, String>),
    Noted(Result<String, String>),
    /// A plan the person chose is set up (or why not): the engine restarts with this model.
    Subscribed(String, Result<(), String>),
    /// This inference's models, read afresh (/model, or while there is no model yet).
    Models(Result<Vec<String>, String>),
}

/// This inference's models as the relay lists them now, for the person's key: the inference's
/// own, the person's own APIs and the computers' models that joined since the session started.
fn models_job(spec: &EngineSpec) -> LocalBoxFuture<'static, Job> {
    let (base, key) = (spec.base_url.clone(), spec.api_key.clone());
    async move { Job::Models(live_models(base, key).await) }.boxed_local()
}

async fn live_models(base: String, key: String) -> Result<Vec<String>, String> {
    match crate::native::llm::Llm::new(&base, &key) {
        Ok(llm) => llm.models().await,
        Err(error) => Err(error.to_string()),
    }
}

fn subscribe_job(keeper: Arc<Keeper>, plan: String, using: String, model: String) -> LocalBoxFuture<'static, Job> {
    async move { Job::Subscribed(model, keeper.subscribe(&plan, &using).await) }.boxed_local()
}

fn list_job(keeper: Arc<Keeper>) -> LocalBoxFuture<'static, Job> {
    async move { Job::Listed(keeper.list().await) }.boxed_local()
}

fn open_job(keeper: Arc<Keeper>, id: String) -> LocalBoxFuture<'static, Job> {
    async move { Job::Opened(keeper.open(&id).await) }.boxed_local()
}

/// The model's notes on the earlier part of a chat, through the relay.
fn notes_job(spec: &EngineSpec, model: String, text: String) -> LocalBoxFuture<'static, Job> {
    let (base, key, most) = (spec.base_url.clone(), spec.api_key.clone(), spec.max_output.min(1536));
    async move { Job::Noted(history::summarize(&base, &key, &model, &text, most).await) }.boxed_local()
}

fn start_job(spec: EngineSpec) -> LocalBoxFuture<'static, Job> {
    async move { Job::Started(crate::engines::start(&spec).await) }.boxed_local()
}

async fn wait_closed(engine: &mut EngineHandle, limit: Duration) {
    let _ = tokio::time::timeout(limit, async { while engine.events.recv().await.is_some() {} }).await;
}

/// The terminal can no longer be written to: the engine is stopped and the screen ends.
async fn terminal_lost(engine: &mut Option<EngineHandle>, error: std::io::Error) -> anyhow::Error {
    if let Some(mut handle) = engine.take() {
        let _ = handle.commands.send(Command::Shutdown);
        wait_closed(&mut handle, STOP_WAIT).await;
    }
    anyhow::anyhow!("The terminal closed: {error}")
}

/// Carries out what a finished job means for the engine and the screen.
fn agent_job(
    job: Job,
    app: &mut App,
    engine: &mut Option<EngineHandle>,
    jobs: &FuturesUnordered<LocalBoxFuture<'static, Job>>,
    quitting: bool,
    now: Instant,
) {
    if quitting {
        // An engine that just started is stopped too; nothing new starts.
        if let Job::Started(Ok(handle)) | Job::Switched(_, _, Ok(handle)) | Job::Reverted(_, _, Ok(handle)) = job {
            let _ = handle.commands.send(Command::Shutdown);
            if engine.is_none() {
                *engine = Some(handle);
            }
        }
        return;
    }
    match job {
        Job::Started(Ok(handle)) => *engine = Some(handle),
        Job::Started(Err(error)) => app.start_failed(&format!("{error:#}")),
        Job::Respecced(model, Ok(next)) => {
            let old = engine.take();
            // The running turn and its open requests end with the engine.
            app.switch_prepared();
            jobs.push(
                async move {
                    if let Some(mut old) = old {
                        let _ = old.commands.send(Command::Shutdown);
                        wait_closed(&mut old, SWITCH_WAIT).await;
                    }
                    Job::OldStopped(model, next)
                }
                .boxed_local(),
            );
        }
        Job::Respecced(model, Err(error)) => {
            app.switch_refused(&model, &format!("{error:#}"), now);
            if engine.is_none() {
                // The engine's events ended while the new model was prepared: it is gone.
                app.engine_closed();
            }
        }
        Job::OldStopped(model, next) => {
            app.switch_step(&format!("starting {} with {}", next.name, crate::spec::shown_model(&model)));
            jobs.push(
                async move {
                    let result = crate::engines::start(&next).await;
                    Job::Switched(model, next, result)
                }
                .boxed_local(),
            );
        }
        Job::Switched(model, next, Ok(handle)) => {
            *engine = Some(handle);
            app.switched(next, &model, now);
        }
        Job::Switched(model, _, Err(error)) => {
            // The new model did not start: start the one before it again.
            let error = format!("{error:#}");
            app.switch_step(&format!("starting {} with {} again", app.name, crate::spec::shown_model(&app.model)));
            let previous = app.spec.clone();
            jobs.push(
                async move {
                    let result = crate::engines::start(&previous).await;
                    Job::Reverted(model, error, result)
                }
                .boxed_local(),
            );
        }
        Job::Reverted(model, error, Ok(handle)) => {
            *engine = Some(handle);
            app.switch_reverted(&model, &error, now);
        }
        Job::Reverted(model, error, Err(again)) => app.switch_failed(&model, &error, &format!("{again:#}")),
        Job::Listed(listing) => app.listed(listing),
        Job::Opened(chat) => app.opened(chat, now),
        Job::Resumed(chat) => app.resumed(chat, now),
        Job::Noted(notes) => app.noted(notes, now),
        Job::Subscribed(model, outcome) => app.subscribed(model, outcome, now),
        Job::Models(listed) => app.models_listed(listed),
        // Taken by the run loop before this.
        Job::Changed(_) => {}
    }
}

#[allow(clippy::too_many_arguments)]
async fn run_agent(
    terminal: &mut Terminal,
    spec: EngineSpec,
    engine: Option<EngineHandle>,
    theme: Theme,
    masker: Masker,
    carry: Carry,
    books: &mut Books,
    resume: Option<String>,
) -> anyhow::Result<Ended> {
    let now = Instant::now();
    let owns = engine.is_none();
    let mut app = App::new(spec, theme, masker, owns, now);
    app.carried(carry);
    // The harness switch under way: how to stop it, and its progress.
    let mut switch_cancel: Option<oneshot::Sender<()>> = None;
    let mut switch_progress: Option<mpsc::UnboundedReceiver<String>> = None;
    // Until the terminal says it reports Shift+Enter, it arrives as Enter (Windows reports it).
    app.shift_enter = cfg!(windows);
    let mut engine = engine;
    // The engine's own jobs (its start, a switch), and beside them the chats' (history.rs).
    let mut jobs: FuturesUnordered<LocalBoxFuture<'static, Job>> = FuturesUnordered::new();
    let mut side: FuturesUnordered<LocalBoxFuture<'static, Job>> = FuturesUnordered::new();
    if owns {
        jobs.push(start_job(app.spec.clone()));
    }
    // The chat a switch into this new VM brought: it opens while the engine starts, and prompts
    // wait for it.
    if let Some(id) = resume {
        app.resuming = true;
        let keeper = books.keeper.clone();
        side.push(async move { Job::Resumed(keeper.open(&id).await) }.boxed_local());
    }
    let mut io = Io::new();
    let mut ticker = tokio::time::interval(TICK);
    ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let mut signals = Signals::new();
    let mut quitting: Option<Instant> = None;
    loop {
        let now = Instant::now();
        // A frame waits while the one before it is still being written.
        if app.dirty && !terminal.writing() && io.wait(now).is_zero() {
            let area = io.screen.area();
            let mut buffer = Buffer::empty(area);
            let frame = draw::draw(&mut app, area, &mut buffer, now);
            let bytes = io.screen.frame(&buffer, frame.cursor, frame.scroll);
            if let Err(error) = terminal.write(bytes) {
                return Err(terminal_lost(&mut engine, error).await);
            }
            app.dirty = false;
            io.last_frame = Some(now);
        }
        let wait = if app.dirty && !terminal.writing() { io.wait(now) } else { Duration::from_secs(3600) };
        tokio::select! {
            written = terminal.written() => {
                if let Err(error) = written {
                    return Err(terminal_lost(&mut engine, error).await);
                }
            }
            shift_enter = detected(&mut io.detect) => {
                io.detect = None;
                io.input = Some(EventStream::new());
                app.shift_enter = cfg!(windows) || shift_enter;
                app.dirty = true;
            }
            event = next_input(&mut io.input) => match event {
                Some(Ok(TermEvent::Key(key))) => app.key(key, Instant::now()),
                Some(Ok(TermEvent::Paste(text))) => app.paste(&text),
                Some(Ok(TermEvent::Resize(width, height))) => {
                    io.resize(width, height);
                    app.dirty = true;
                }
                Some(Ok(_)) => {}
                Some(Err(_)) | None => {
                    // No more keys can come: quit.
                    io.input = None;
                    app.quit();
                }
            },
            event = next_event(&mut engine) => match event {
                Some(event) => {
                    let now = Instant::now();
                    app.apply(event, now);
                    if let Some(handle) = engine.as_mut() {
                        for _ in 0..BATCH {
                            match handle.events.try_recv() {
                                Ok(event) => app.apply(event, now),
                                Err(_) => break,
                            }
                        }
                    }
                }
                None => {
                    engine = None;
                    if quitting.is_none() {
                        app.engine_closed();
                    }
                }
            },
            Some(step) = next_progress(&mut switch_progress) => app.change_step(&step),
            Some(outcome) = books.saved.recv() => app.saved(outcome),
            Some(job) = side.next(), if !side.is_empty() => {
                agent_job(job, &mut app, &mut engine, &jobs, quitting.is_some(), Instant::now());
            }
            Some(job) = jobs.next(), if !jobs.is_empty() => {
                let now = Instant::now();
                match job {
                    Job::Respecced(_, Ok(next)) if quitting.is_none() && !next.sign_in.is_empty() => {
                        // A plan the client is not signed in to yet: the engine stops, the client's
                        // sign-in runs in the terminal pane, then the engine starts on the plan.
                        app.switch_prepared();
                        if let Some(mut handle) = engine.take() {
                            let _ = handle.commands.send(Command::Shutdown);
                            wait_closed(&mut handle, SWITCH_WAIT).await;
                        }
                        app.save_chat();
                        take_saves(&mut app.effects, &books.saver);
                        let back = Box::new(app.spec.clone());
                        let carry = app.carry(&next);
                        return Ok(Ended::SignIn(next.into(), back, Box::new(carry)));
                    }
                    Job::Respecced(_, Ok(next)) if quitting.is_none() && next.protocol == Protocol::Terminal => {
                        // The harness runs in the terminal pane (a session that had no model
                        // yet): the pane takes over, on the model chosen.
                        app.switch_prepared();
                        if let Some(mut handle) = engine.take() {
                            let _ = handle.commands.send(Command::Shutdown);
                            wait_closed(&mut handle, SWITCH_WAIT).await;
                        }
                        app.save_chat();
                        take_saves(&mut app.effects, &books.saver);
                        let carry = app.carry(&next);
                        return Ok(Ended::Switch(Box::new(next), Box::new(carry)));
                    }
                    Job::Changed(switched) if quitting.is_none() => {
                        switch_cancel = None;
                        switch_progress = None;
                        match switched {
                            Switched::InPlace(next) => {
                                // Kept as it stands, whatever happens to the next harness.
                                app.save_chat();
                                take_saves(&mut app.effects, &books.saver);
                                let carry = app.carry(&next);
                                return Ok(Ended::Switch(next, Box::new(carry)));
                            }
                            Switched::NewVm => {
                                // The launcher starts the next VM once this screen has ended; the
                                // chat it goes on with is saved first.
                                app.save_chat();
                                take_saves(&mut app.effects, &books.saver);
                                books.saver.flush(SAVE_WAIT).await;
                                if let Some(mut handle) = engine.take() {
                                    let _ = handle.commands.send(Command::Shutdown);
                                    wait_closed(&mut handle, STOP_WAIT).await;
                                }
                                return Ok(Ended::Exit(NEW_VM_EXIT));
                            }
                            Switched::Failed { reason, kept } => {
                                let before = app.spec.harness == YOUGORI;
                                if app.change_failed(&reason, now) {
                                    if kept || before {
                                        engine = None;
                                        jobs.push(start_job(app.spec.clone()));
                                    } else {
                                        // The harness before is gone from the VM: Yougori's own
                                        // agent needs nothing to start.
                                        let back = Pending::yougori(&app.spec, &app.switch_model());
                                        app.start_switch(back, now);
                                    }
                                }
                            }
                        }
                    }
                    job => agent_job(job, &mut app, &mut engine, &jobs, quitting.is_some(), now),
                }
            }
            _ = ticker.tick() => {
                let now = Instant::now();
                app.poll_models(now);
                for panic in Terminal::panics() {
                    app.transcript.notice(Level::Error, &format!("Internal error: {panic}"));
                    app.dirty = true;
                }
                if app.animating(now) {
                    app.dirty = true;
                }
            }
            _ = tokio::time::sleep(wait), if app.dirty => {}
            _ = signals.next() => app.quit(),
        }
        let now = Instant::now();
        for effect in std::mem::take(&mut app.effects) {
            match effect {
                Effect::Send(command) => {
                    let sent = engine.as_ref().is_some_and(|handle| handle.commands.send(command).is_ok());
                    if !sent && quitting.is_none() && app.phase == Phase::Ready {
                        app.engine_closed();
                    }
                }
                Effect::Start => {
                    if owns && jobs.is_empty() && quitting.is_none() {
                        engine = None;
                        app.starting(now);
                        jobs.push(start_job(app.spec.clone()));
                    }
                }
                Effect::Restart(model) => {
                    let spec = app.spec.clone();
                    jobs.push(
                        async move {
                            let result = crate::engines::respec(&spec, &model).await;
                            Job::Respecced(model, result)
                        }
                        .boxed_local(),
                    );
                }
                Effect::Switch(pending) => {
                    // In place the engine stops before its pack can be replaced; a new VM's switch
                    // leaves it running until the launcher takes over, and names the chat it goes
                    // on with.
                    let mut request = pending.request();
                    if !app.chat.is_empty() {
                        request["chat"] = serde_json::Value::String(app.chat.id.clone());
                    }
                    let old = if pending.mode == Where::InPlace { engine.take() } else { None };
                    let (progress, received) = mpsc::unbounded_channel();
                    let (cancel, cancelled) = oneshot::channel();
                    switch_progress = Some(received);
                    switch_cancel = Some(cancel);
                    let spec = app.spec.clone();
                    jobs.push(
                        async move {
                            if let Some(mut old) = old {
                                let _ = old.commands.send(Command::Shutdown);
                                wait_closed(&mut old, SWITCH_WAIT).await;
                            }
                            Job::Changed(switcher::run(spec, request, progress, cancelled).await)
                        }
                        .boxed_local(),
                    );
                }
                Effect::CancelSwitch => {
                    if let Some(cancel) = switch_cancel.take() {
                        let _ = cancel.send(());
                    }
                }
                Effect::Redraw => {
                    io.screen.invalidate();
                    app.dirty = true;
                }
                Effect::Save(chat) => books.saver.save(*chat),
                Effect::History => side.push(list_job(books.keeper.clone())),
                Effect::Open(id) => side.push(open_job(books.keeper.clone(), id)),
                Effect::Subscribe { plan, using, model } => {
                    // A step of the model switch (its phase says so): one of the engine's jobs.
                    jobs.push(subscribe_job(books.keeper.clone(), plan, using, model));
                }
                Effect::Notes { text } => match app.notes_model() {
                    Some(model) => side.push(notes_job(&app.spec, model, text)),
                    None => app.noted(Err("no model of this inference can write them".into()), now),
                },
                Effect::Models => side.push(models_job(&app.spec)),
                Effect::Renew => {
                    // The engine starts again, fresh: a new chat for an engine without a /new.
                    if owns && quitting.is_none() {
                        let old = engine.take();
                        app.starting(now);
                        let spec = app.spec.clone();
                        jobs.push(
                            async move {
                                if let Some(mut old) = old {
                                    let _ = old.commands.send(Command::Shutdown);
                                    wait_closed(&mut old, SWITCH_WAIT).await;
                                }
                                Job::Started(crate::engines::start(&spec).await)
                            }
                            .boxed_local(),
                        );
                    }
                }
                Effect::Quit => {
                    if quitting.is_none() {
                        quitting = Some(now);
                        app.phase = Phase::Quitting { since: now };
                        app.dirty = true;
                        if let Some(handle) = engine.as_ref() {
                            let _ = handle.commands.send(Command::Shutdown);
                        }
                        if let Some(cancel) = switch_cancel.take() {
                            let _ = cancel.send(());
                        }
                    }
                }
            }
        }
        if let Some(since) = quitting {
            if (engine.is_none() && jobs.is_empty()) || now.saturating_duration_since(since) >= STOP_WAIT {
                break;
            }
        }
    }
    // Dropping the handle stops whatever is left of the engine and its services.
    drop(engine);
    // The chat is kept as it ended.
    app.save_chat();
    take_saves(&mut app.effects, &books.saver);
    books.saver.flush(SAVE_WAIT).await;
    Ok(Ended::Exit(app.exit_code()))
}

/// Sends the saves among `effects` to the saver, and leaves the others.
fn take_saves(effects: &mut Vec<Effect>, saver: &Saver) {
    effects.retain(|effect| match effect {
        Effect::Save(chat) => {
            saver.save((**chat).clone());
            false
        }
        _ => true,
    });
}

/// A model switch in the terminal pane. There is at most one, so its size does not matter.
#[allow(clippy::large_enum_variant)]
enum Switch {
    /// Starting the new model's client; `previous` is what to go back to.
    Trying { model: String, previous: EngineSpec },
    /// The new one failed; the old one is starting again.
    Reverting { note: String },
}

/// A client asked to stop so the next one can start (/new, another model): the next one starts
/// once it has ended, so what it started (a database, a server) has stopped first, or after
/// RENEW_WAIT when it does not end.
struct Stopping {
    child: pane::Child,
    since: Instant,
    next: EngineSpec,
    ended: bool,
}

/// Marks the stopping client ended when its end arrives.
fn note_end(stopping: &mut Option<Stopping>, message: &pane::Message) {
    if let (Some(stop), pane::Message::Exited(generation, _)) = (stopping.as_mut(), message) {
        if *generation == stop.child.generation {
            stop.ended = true;
        }
    }
}

/// Work the terminal pane waits for besides output and keys. A few at a time, so their size does
/// not matter.
#[allow(clippy::large_enum_variant)]
enum PaneJob {
    Started(u64, anyhow::Result<pane::Child>),
    Respecced(String, anyhow::Result<EngineSpec>),
    /// A harness switch ended (/harness).
    Changed(Switched),
    /// /history's list, a chat opened from it, and the chat the session goes on with (before the
    /// first client starts).
    Listed(Result<Listing, String>),
    Opened(Result<Chat, String>),
    Resumed(Result<Chat, String>),
    /// This inference's models, read afresh (/model).
    Models(Result<Vec<String>, String>),
}

/// Starts the spec's client; with a `takeover`, the earlier chat is written for it first.
fn pane_start(
    spec: EngineSpec,
    rows: u16,
    cols: u16,
    generation: u64,
    depth: Depth,
    tx: tokio::sync::mpsc::UnboundedSender<pane::Message>,
    takeover: Option<pane::Takeover>,
) -> LocalBoxFuture<'static, PaneJob> {
    async move {
        let result = async {
            let mut spec = spec;
            if let Some(takeover) = takeover {
                if let Some(file) = takeover.write().await {
                    spec.env.insert(pane::CONTEXT_FILE.into(), file);
                }
            }
            let services = crate::engines::services::start(&spec).await?;
            pane::spawn(&spec, rows, cols, generation, Some(services), depth, tx)
        }
        .await;
        PaneJob::Started(generation, result)
    }
    .boxed_local()
}

#[allow(clippy::too_many_arguments)]
async fn run_pane(
    terminal: &mut Terminal,
    spec: EngineSpec,
    theme: Theme,
    masker: Masker,
    carry: Carry,
    books: &mut Books,
    resume: Option<String>,
    mut sign_in: Option<SignIn>,
) -> anyhow::Result<Ended> {
    let now = Instant::now();
    let mut io = Io::new();
    let size = io.screen.area();
    let mut pane = Pane::new(spec, theme, masker, size.width, size.height, now);
    if !carry.note.is_empty() && sign_in.is_none() {
        pane.note = Some((format!("Switched to {}", carry.note), now));
    }
    let mut carry = carry;
    // A client's sign-in: the conversation and the chat pass through it to the spec after it.
    if let Some(sign) = &sign_in {
        pane.signing = true;
        pane.back = sign.back.as_ref().map(|back| back.model.clone());
    }
    if let Some(mut chat) = carry.chat.take().filter(|_| sign_in.is_none()) {
        // The chat goes on here: a client that takes it gets it when it starts.
        chat.entered(&pane.spec.harness, &pane.spec.name, &pane.spec.model);
        pane.chat = chat;
        pane.given = false;
    }
    pane.kept = carry;
    // The harness switch under way: how to stop it, and its progress.
    let mut switch_cancel: Option<oneshot::Sender<()>> = None;
    let mut switch_progress: Option<mpsc::UnboundedReceiver<String>> = None;
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<pane::Message>();
    // The client's own jobs (its start, a switch), and beside them the chats' (history.rs), which
    // never hold up a start.
    let mut jobs: FuturesUnordered<LocalBoxFuture<'static, PaneJob>> = FuturesUnordered::new();
    let mut side: FuturesUnordered<LocalBoxFuture<'static, PaneJob>> = FuturesUnordered::new();
    let mut ticker = tokio::time::interval(TICK);
    ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let mut signals = Signals::new();
    let mut quitting: Option<Instant> = None;
    // A model switch in progress.
    let mut switching: Option<Switch> = None;
    // A client stopping for the next one.
    let mut stopping: Option<Stopping> = None;
    let mut next_generation = 1u64;
    let mut last_second = 0u64;
    match resume {
        // The chat this session goes on with first: the client starts with it.
        Some(id) => {
            let keeper = books.keeper.clone();
            side.push(async move { PaneJob::Resumed(keeper.open(&id).await) }.boxed_local());
        }
        None => pane.effects.push(PaneEffect::Start),
    }
    loop {
        let now = Instant::now();
        let mut start: Option<EngineSpec> = None;
        for effect in std::mem::take(&mut pane.effects) {
            match effect {
                PaneEffect::Start => {
                    if jobs.is_empty() && stopping.is_none() && quitting.is_none() {
                        pane.child = None;
                        pane.phase = PanePhase::Starting { since: now };
                        // A fresh client has none of the chat: one that takes it gets it again.
                        pane.given = false;
                        start = Some(pane.spec.clone());
                    }
                }
                PaneEffect::Renew => {
                    if quitting.is_none() && jobs.is_empty() && stopping.is_none() {
                        pane.renewing(now);
                        match pane.child.take() {
                            Some(mut child) => {
                                child.kill();
                                // What it prints while it stops is its own: the next screen is fresh.
                                pane.generation = 0;
                                stopping = Some(Stopping { child, since: now, next: pane.spec.clone(), ended: false });
                            }
                            None => start = Some(pane.spec.clone()),
                        }
                    } else if quitting.is_none() {
                        pane.wait_to_renew(now);
                    }
                }
                PaneEffect::Restart(model) => {
                    if quitting.is_none() && jobs.is_empty() && stopping.is_none() {
                        pane.switch_preparing(&model, now);
                        let spec = pane.spec.clone();
                        jobs.push(
                            async move {
                                let result = crate::engines::respec(&spec, &model).await;
                                PaneJob::Respecced(model, result)
                            }
                            .boxed_local(),
                        );
                    } else if quitting.is_none() {
                        // A start or another switch is under way (the palette does not offer it then).
                        pane.wait_to_switch(now);
                    }
                }
                PaneEffect::Switch(pending) => {
                    // In place the client stops before its pack can be replaced (its services stop
                    // with it); a new VM's switch leaves it running until the launcher takes over.
                    // The chat is kept as it stands first, and goes on in the next harness.
                    pane.save_chat();
                    if pending.mode == Where::InPlace {
                        if let Some(mut child) = pane.child.take() {
                            child.kill();
                        }
                    }
                    let mut request = pending.request();
                    if !pane.chat.is_empty() {
                        request["chat"] = serde_json::Value::String(pane.chat.id.clone());
                    }
                    let (progress, received) = mpsc::unbounded_channel();
                    let (cancel, cancelled) = oneshot::channel();
                    switch_progress = Some(received);
                    switch_cancel = Some(cancel);
                    let spec = pane.spec.clone();
                    jobs.push(
                        async move { PaneJob::Changed(switcher::run(spec, request, progress, cancelled).await) }
                            .boxed_local(),
                    );
                }
                PaneEffect::CancelSwitch => {
                    if let Some(cancel) = switch_cancel.take() {
                        let _ = cancel.send(());
                    }
                }
                PaneEffect::Redraw => io.screen.invalidate(),
                PaneEffect::Save(chat) => books.saver.save(*chat),
                PaneEffect::History => {
                    let keeper = books.keeper.clone();
                    side.push(async move { PaneJob::Listed(keeper.list().await) }.boxed_local());
                }
                PaneEffect::Models => {
                    let (base, key) = (pane.spec.base_url.clone(), pane.spec.api_key.clone());
                    side.push(async move { PaneJob::Models(live_models(base, key).await) }.boxed_local());
                }
                PaneEffect::Open(id) => {
                    let keeper = books.keeper.clone();
                    side.push(async move { PaneJob::Opened(keeper.open(&id).await) }.boxed_local());
                }
                PaneEffect::Back => {
                    // The sign-in did not finish: back to the model before it, or the session ends.
                    drop(pane.child.take());
                    return Ok(match sign_in.and_then(|sign| sign.back) {
                        Some(back) => {
                            let mut carry = std::mem::take(&mut pane.kept);
                            carry.note = format!("{} · {}", back.name, crate::spec::shown_model(&back.model));
                            Ended::Switch(back, Box::new(carry))
                        }
                        None => Ended::Exit(0),
                    });
                }
                PaneEffect::Quit => {
                    if quitting.is_none() {
                        quitting = Some(now);
                        pane.phase = PanePhase::Quitting { since: now };
                        if let Some(child) = pane.child.as_mut() {
                            child.kill();
                        }
                        if let Some(cancel) = switch_cancel.take() {
                            let _ = cancel.send(());
                        }
                    }
                }
            }
            pane.dirty = true;
        }
        if let Some(spec) = start {
            let (rows, cols) = pane::pane_size(io.screen.area().width, io.screen.area().height);
            jobs.push(pane_start(spec, rows, cols, next_generation, pane.theme.depth, tx.clone(), pane.takeover()));
            pane.generation = next_generation;
            next_generation += 1;
        }
        // The client stopping for the next one has ended (or had its time): the next one starts.
        if stopping.as_ref().is_some_and(|s| s.ended || now.saturating_duration_since(s.since) >= pane::RENEW_WAIT) {
            if let Some(Stopping { child, next, .. }) = stopping.take() {
                drop(child);
                if quitting.is_none() {
                    let (rows, cols) = pane::pane_size(io.screen.area().width, io.screen.area().height);
                    jobs.push(pane_start(next, rows, cols, next_generation, pane.theme.depth, tx.clone(), pane.takeover()));
                    pane.generation = next_generation;
                    next_generation += 1;
                }
                pane.dirty = true;
            }
        }
        // The client signed in: the spec it was for starts, with the conversation.
        if pane.signing && quitting.is_none() && matches!(pane.phase, PanePhase::Ended { failed: false, .. }) {
            if let Some(sign) = sign_in.take() {
                let mut then = sign.then;
                then.sign_in.clear();
                return Ok(Ended::Switch(then, Box::new(std::mem::take(&mut pane.kept))));
            }
        }
        if let Some(since) = quitting {
            let ended = pane.child.is_none() && stopping.as_ref().is_none_or(|s| s.ended);
            if ended || now.saturating_duration_since(since) >= pane::STOP_WAIT {
                break;
            }
        }
        // A frame waits while the one before it is still being written.
        if pane.dirty && !terminal.writing() && io.wait(now).is_zero() {
            let area = io.screen.area();
            let mut buffer = Buffer::empty(area);
            let frame = pane.draw(area, &mut buffer, now);
            let bytes = io.screen.frame(&buffer, frame.cursor, frame.scroll);
            if let Err(error) = terminal.write(bytes) {
                return Err(anyhow::anyhow!("The terminal closed: {error}"));
            }
            pane.dirty = false;
            io.last_frame = Some(now);
        }
        let wait = if pane.dirty && !terminal.writing() { io.wait(now) } else { Duration::from_secs(3600) };
        tokio::select! {
            written = terminal.written() => {
                if let Err(error) = written {
                    return Err(anyhow::anyhow!("The terminal closed: {error}"));
                }
            }
            _ = detected(&mut io.detect) => {
                io.detect = None;
                io.input = Some(EventStream::new());
            }
            event = next_input(&mut io.input) => match event {
                Some(Ok(TermEvent::Key(key))) => pane.key(key, Instant::now()),
                Some(Ok(TermEvent::Paste(text))) => pane.paste(&text),
                Some(Ok(TermEvent::Resize(width, height))) => {
                    io.resize(width, height);
                    pane.resize(width.max(1), height.max(1));
                }
                Some(Ok(_)) => {}
                Some(Err(_)) | None => {
                    io.input = None;
                    pane.effects.push(PaneEffect::Quit);
                }
            },
            Some(message) = rx.recv() => {
                let now = Instant::now();
                note_end(&mut stopping, &message);
                pane.message(message, now);
                for _ in 0..BATCH {
                    match rx.try_recv() {
                        Ok(message) => {
                            note_end(&mut stopping, &message);
                            pane.message(message, now);
                        }
                        Err(_) => break,
                    }
                }
            }
            Some(step) = next_progress(&mut switch_progress) => pane.change_step(&step),
            Some(job) = side.next(), if !side.is_empty() => {
                if quitting.is_none() {
                    pane_chat_job(&mut pane, job, Instant::now());
                }
            }
            Some(job) = jobs.next(), if !jobs.is_empty() => {
                let now = Instant::now();
                match job {
                    PaneJob::Changed(_) if quitting.is_some() => {}
                    PaneJob::Changed(switched) => {
                        switch_cancel = None;
                        switch_progress = None;
                        match switched {
                            Switched::InPlace(next) => {
                                drop(pane.child.take());
                                take_pane_saves(&mut pane.effects, &books.saver);
                                let mut carry = std::mem::take(&mut pane.kept);
                                carry.note = format!("{} · {}", next.name, crate::spec::shown_model(&next.model));
                                carry.chat = Some(pane.chat.clone());
                                return Ok(Ended::Switch(next, Box::new(carry)));
                            }
                            Switched::NewVm => {
                                // The launcher starts the next VM once this screen has ended; the
                                // chat it goes on with is saved first, as it stands now (the client
                                // ran on while the VM was made).
                                pane.save_chat();
                                drop(pane.child.take());
                                take_pane_saves(&mut pane.effects, &books.saver);
                                books.saver.flush(SAVE_WAIT).await;
                                return Ok(Ended::Exit(NEW_VM_EXIT));
                            }
                            Switched::Failed { reason, kept } => {
                                if pane.change_failed(&reason, now) {
                                    if kept {
                                        pane.effects.push(PaneEffect::Start);
                                    } else {
                                        // The harness before is gone from the VM: Yougori's own
                                        // agent needs nothing to start.
                                        let back = Pending::yougori(&pane.spec, &pane.spec.model);
                                        pane.start_switch(back, now);
                                    }
                                }
                            }
                        }
                    }
                    PaneJob::Started(generation, Ok(child)) => {
                        if quitting.is_some() || generation != pane.generation {
                            drop(child);
                        } else {
                            pane.started(child, now);
                            if let Some(Switch::Reverting { note }) = switching.take() {
                                pane.note = Some((note, now));
                            }
                        }
                    }
                    PaneJob::Started(_, Err(error)) => {
                        let error = format!("{error:#}");
                        match switching.take() {
                            Some(Switch::Trying { model, previous }) => {
                                // The new model's client did not start: go back to the old one,
                                // which gets the chat again.
                                let note = format!("Could not start {} with {model}: {error}. Back on {}.", pane.spec.name, previous.model);
                                pane.given = false;
                                pane.spec = previous.clone();
                                pane.phase = PanePhase::Switching { model: previous.model.clone(), since: now, step: "Starting again".into() };
                                let (rows, cols) = pane::pane_size(io.screen.area().width, io.screen.area().height);
                                jobs.push(pane_start(previous, rows, cols, next_generation, pane.theme.depth, tx.clone(), pane.takeover()));
                                pane.generation = next_generation;
                                next_generation += 1;
                                switching = Some(Switch::Reverting { note });
                            }
                            Some(Switch::Reverting { note }) => pane.start_failed(&format!("{note} Starting it again failed too: {error}")),
                            None => pane.start_failed(&error),
                        }
                    }
                    PaneJob::Respecced(model, Ok(next)) => {
                        // Stop the running client; the new one starts in a fresh screen once it
                        // has ended, with the chat as it stands (a client that takes it).
                        pane.save_chat();
                        pane.given = false;
                        pane.switch_from = None;
                        switching = Some(Switch::Trying { model: model.clone(), previous: pane.spec.clone() });
                        pane.spec = next.clone();
                        pane.phase = PanePhase::Switching { model, since: now, step: "Starting".into() };
                        match pane.child.take() {
                            Some(mut child) => {
                                child.kill();
                                pane.generation = 0;
                                stopping = Some(Stopping { child, since: now, next, ended: false });
                            }
                            None => {
                                let (rows, cols) = pane::pane_size(io.screen.area().width, io.screen.area().height);
                                jobs.push(pane_start(next, rows, cols, next_generation, pane.theme.depth, tx.clone(), pane.takeover()));
                                pane.generation = next_generation;
                                next_generation += 1;
                            }
                        }
                    }
                    PaneJob::Respecced(model, Err(error)) => pane.switch_refused(&model, &format!("{error:#}"), now),
                    // The chats' jobs run beside these (`side`).
                    job @ (PaneJob::Listed(_) | PaneJob::Opened(_) | PaneJob::Resumed(_) | PaneJob::Models(_)) => {
                        pane_chat_job(&mut pane, job, now)
                    }
                }
                pane.dirty = true;
            }
            Some(outcome) = books.saved.recv() => pane.saved(outcome, Instant::now()),
            _ = ticker.tick() => {
                let now = Instant::now();
                let second = now.saturating_duration_since(pane.epoch).as_secs();
                if pane.animating() || pane.note_pending(now) || (pane.running() && second != last_second) {
                    pane.dirty = true;
                }
                last_second = second;
            }
            _ = tokio::time::sleep(wait), if pane.dirty => {}
            _ = signals.next() => pane.effects.push(PaneEffect::Quit),
        }
    }
    // The chat is kept as it ended (the client's screen is still in the pane).
    pane.save_chat();
    drop(pane.child.take());
    take_pane_saves(&mut pane.effects, &books.saver);
    books.saver.flush(SAVE_WAIT).await;
    Ok(Ended::Exit(pane.exit_code()))
}

/// What a chats' job of the terminal pane brought: /history's list, a chat opened from it, or the
/// chat the session goes on with, which the first client starts with.
fn pane_chat_job(pane: &mut Pane, job: PaneJob, now: Instant) {
    match job {
        PaneJob::Listed(listing) => pane.listed(listing, now),
        PaneJob::Models(listed) => pane.models_listed(listed, now),
        PaneJob::Opened(chat) => pane.opened(chat, now),
        PaneJob::Resumed(chat) => {
            match chat {
                Ok(mut chat) => {
                    chat.entered(&pane.spec.harness, &pane.spec.name, &pane.spec.model);
                    pane.note = Some((format!("Going on with “{}”.", pane.masker.mask(&chat.title)), now));
                    pane.chat = chat;
                    pane.given = false;
                }
                Err(error) => pane.note = Some((format!("Could not open the chat to go on with: {}", error.trim()), now)),
            }
            pane.effects.push(PaneEffect::Start);
        }
        _ => {}
    }
    pane.dirty = true;
}

/// Sends the saves among a pane's `effects` to the saver, and leaves the others.
fn take_pane_saves(effects: &mut Vec<PaneEffect>, saver: &Saver) {
    effects.retain(|effect| match effect {
        PaneEffect::Save(chat) => {
            saver.save((**chat).clone());
            false
        }
        _ => true,
    });
}
