//! The person's chats, kept on their own computer.
//!
//! The screen records the chat as it goes ([`Chat`]): each prompt, each reply and the titles of the
//! tools that ran for it, in whichever harness, and what a terminal harness showed. After every
//! turn, and before it ends or switches, it saves the chat through the VM's guest
//! (vm_guest.py `/history/save`, with the session's switch token), which hands it to the launcher
//! on the person's computer; the launcher keeps it in the inference's private folder
//! (vm_host.py, `history`). /history lists the chats kept there and this session's, and opens one
//! again in the harness that runs.
//!
//! A harness that takes over a chat it has not seen (after a /harness switch, a model switch that
//! restarts it, or a chat opened from /history) gets the earlier turns with the next prompt,
//! compacted to fit its window: the newest turns whole, the older ones as the model's notes on
//! them ([`summarize`]), or, when the model cannot write them, left out with a line that says so.
use crate::native::cancel::Cancel;
use crate::native::llm::{Llm, Request};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

/// The VM's guest, on the loopback, and the header its token goes in.
pub const GUEST: &str = "http://127.0.0.1:8090";
pub const HEADER: &str = "X-Yougori-Switch";
/// The variable the VM gives the screen alone (as for switching harness).
pub const TOKEN: &str = "YOUGORI_SWITCH_TOKEN";
/// How much of one text a chat keeps, how much of a terminal harness's screen, and how large a
/// whole chat may be (its oldest turns go first).
const TEXT_BYTES: usize = 24 * 1024;
const SCREEN_BYTES: usize = 96 * 1024;
const CHAT_BYTES: usize = 768 * 1024;
/// Tools kept by title for one reply.
const TOOLS: usize = 40;
const TITLE_CHARS: usize = 72;
/// How long the guest may take to answer (an open asks the launcher on the person's computer).
const GUEST_WAIT: Duration = Duration::from_secs(40);

/// The model's instructions for compacting an earlier conversation, in our own words.
pub const SUMMARY_PROMPT: &str = "You condense the earlier part of a conversation between a person and an AI assistant, \
so that another assistant can take the conversation over. Write short notes under these headings:
Asked: what the person asked for, keeping their exact words for requirements, names and values.
Done: what was done: files changed, commands run and what they showed.
Known: facts, decisions and their reasons that still matter.
Open: what was in progress at the end, and what comes next.
Keep paths, names, numbers and error messages exactly as they were. Never invent anything. Reply with the notes only.";

pub fn now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

/// A new chat's id: 32 hexadecimal characters, as the guest and the launcher take them.
pub fn new_id() -> String {
    uuid::Uuid::new_v4().simple().to_string()
}

/// Whether `id` is a chat id.
pub fn valid_id(id: &str) -> bool {
    id.len() == 32 && id.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// The largest index at most `at` that falls on a character boundary of `text`.
fn floor(text: &str, at: usize) -> usize {
    let mut at = at.min(text.len());
    while !text.is_char_boundary(at) {
        at -= 1;
    }
    at
}

/// `text` within `most` bytes: its start and its end, with a line saying what was left out.
pub fn bounded(text: &str, most: usize) -> String {
    if text.len() <= most {
        return text.to_owned();
    }
    let half = most.saturating_sub(40) / 2;
    let head = floor(text, half);
    let mut tail = text.len() - half;
    while !text.is_char_boundary(tail) {
        tail += 1;
    }
    format!("{}\n[… {} characters left out …]\n{}", &text[..head], text[head..tail].chars().count(), &text[tail..])
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Role {
    User,
    Assistant,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Turn {
    pub role: Role,
    pub text: String,
    /// The harness that took the prompt or gave the reply, by name.
    #[serde(default)]
    pub harness: String,
    /// The tools that ran for this reply, by title.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub tools: Vec<String>,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct Chat {
    pub id: String,
    pub title: String,
    pub started: u64,
    pub updated: u64,
    /// Goes up with every save, so the newest save of a chat wins wherever saves cross.
    #[serde(default)]
    pub version: u64,
    /// The harness it is in now (its id), that harness's name, and every harness it was in.
    pub harness: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub harnesses: Vec<String>,
    #[serde(default)]
    pub model: String,
    #[serde(default)]
    pub turns: Vec<Turn>,
    /// What a terminal harness showed (its screen's text, the newest part), for one that gives no
    /// turns of its own.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub screen: String,
}

/// A chat as /history lists it.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct Summary {
    pub id: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub updated: u64,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub harnesses: Vec<String>,
    #[serde(default)]
    pub model: String,
    #[serde(default)]
    pub turns: usize,
}

impl Chat {
    pub fn new(harness: &str, name: &str, model: &str) -> Chat {
        let at = now();
        let mut chat = Chat { id: new_id(), started: at, updated: at, ..Chat::default() };
        chat.entered(harness, name, model);
        chat
    }

    /// Nothing was said in it: no prompt, and no screen kept.
    pub fn is_empty(&self) -> bool {
        !self.turns.iter().any(|turn| turn.role == Role::User) && self.screen.trim().is_empty()
    }

    /// The chat goes on in this harness, with this model.
    pub fn entered(&mut self, harness: &str, name: &str, model: &str) {
        self.harness = harness.to_owned();
        self.name = name.to_owned();
        self.model = model.to_owned();
        if !name.is_empty() && self.harnesses.last().map(String::as_str) != Some(name) {
            self.harnesses.push(name.to_owned());
        }
    }

    /// A prompt the person sent.
    pub fn user(&mut self, text: &str) {
        let text = text.trim();
        if text.is_empty() {
            return;
        }
        if self.title.is_empty() {
            self.title = title(text);
        }
        self.turns.push(Turn { role: Role::User, text: bounded(text, TEXT_BYTES), harness: self.name.clone(), tools: Vec::new() });
        self.updated = now();
    }

    /// The reply this turn, as the harness writes it: `chunk` added to it. `separate` starts a new
    /// paragraph (another message of the same reply).
    pub fn reply(&mut self, chunk: &str, separate: bool) {
        if chunk.is_empty() {
            return;
        }
        let turn = self.reply_turn();
        if separate && !turn.text.trim().is_empty() && !turn.text.ends_with("\n\n") {
            turn.text.push_str("\n\n");
        }
        turn.text.push_str(chunk);
        if turn.text.len() > TEXT_BYTES * 2 {
            turn.text = bounded(&turn.text, TEXT_BYTES);
        }
        self.updated = now();
    }

    /// A tool ran for this reply.
    pub fn tool(&mut self, title: &str) {
        let title: String = title.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(160).collect();
        if title.is_empty() {
            return;
        }
        let turn = self.reply_turn();
        if turn.tools.len() < TOOLS && turn.tools.last() != Some(&title) {
            turn.tools.push(title);
        }
        self.updated = now();
    }

    fn reply_turn(&mut self) -> &mut Turn {
        let name = self.name.clone();
        let needs = !matches!(self.turns.last(), Some(turn) if turn.role == Role::Assistant && turn.harness == name);
        if needs {
            self.turns.push(Turn { role: Role::Assistant, text: String::new(), harness: name, tools: Vec::new() });
        }
        self.turns.last_mut().expect("a reply turn")
    }

    /// What a terminal harness shows now: its newest part is kept.
    pub fn set_screen(&mut self, text: &str) {
        let text = text.trim_end();
        if text.trim().is_empty() {
            return;
        }
        if self.title.is_empty() {
            self.title = format!("{} session", self.name);
        }
        let start = text.len().saturating_sub(SCREEN_BYTES);
        let mut start = start;
        while !text.is_char_boundary(start) {
            start += 1;
        }
        self.screen = text[start..].to_owned();
        self.updated = now();
    }

    pub fn summary(&self) -> Summary {
        Summary {
            id: self.id.clone(),
            title: self.title.clone(),
            updated: self.updated,
            name: self.name.clone(),
            harnesses: self.harnesses.clone(),
            model: self.model.clone(),
            turns: self.turns.len(),
        }
    }

    /// A copy to save: at most CHAT_BYTES of JSON, its oldest turns left out first. Each turn is
    /// measured once (with the comma after it), so a long chat costs no more than its length.
    pub fn to_save(&self) -> Chat {
        let size = |chat: &Chat| serde_json::to_vec(chat).map(|v| v.len()).unwrap_or(usize::MAX);
        let mut whole = size(self);
        let mut first = 0;
        while whole > CHAT_BYTES && first + 1 < self.turns.len() {
            whole = whole.saturating_sub(serde_json::to_vec(&self.turns[first]).map_or(0, |v| v.len() + 1));
            first += 1;
        }
        let mut chat = self.clone();
        chat.turns.drain(..first);
        if size(&chat) > CHAT_BYTES {
            chat.screen = bounded(&chat.screen, SCREEN_BYTES / 4);
        }
        chat
    }
}

/// A chat's title: the first line of its first prompt.
pub fn title(text: &str) -> String {
    let line = text.lines().map(str::trim).find(|line| !line.is_empty()).unwrap_or("");
    let mut title: String = line.chars().take(TITLE_CHARS).collect();
    if line.chars().count() > TITLE_CHARS {
        title.push('…');
    }
    title
}

/// The characters a harness's context may take for the earlier turns: a quarter of its window
/// (about three characters a token), within 4,000 and 96,000.
pub fn budget(window: u32) -> usize {
    (window as usize / 4 * 3).clamp(4_000, 96_000)
}

/// One turn as the next harness reads it.
fn written(turn: &Turn, most: usize) -> String {
    let who = match turn.role {
        Role::User => "User".to_owned(),
        Role::Assistant if turn.harness.is_empty() => "Assistant".to_owned(),
        Role::Assistant => format!("Assistant ({})", turn.harness),
    };
    let mut text = format!("{who}: {}", bounded(turn.text.trim(), most));
    if !turn.tools.is_empty() {
        text.push_str(&format!("\n(tools it ran: {})", turn.tools.join("; ")));
    }
    text
}

/// How the turns split for the context: from `start` on they fit whole within `budget`.
pub fn newest(turns: &[Turn], budget: usize) -> usize {
    let each = (budget / 2).max(1_000);
    let mut used = 0;
    let mut start = turns.len();
    while start > 0 {
        let size = written(&turns[start - 1], each).len() + 2;
        if used + size > budget {
            break;
        }
        used += size;
        start -= 1;
    }
    start
}

/// The earlier turns as the text a summary is asked of, at most `most` characters (the newest
/// of them kept).
pub fn transcript(turns: &[Turn], screen: &str, most: usize) -> String {
    let mut parts: Vec<String> = turns.iter().map(|turn| written(turn, 6_000)).collect();
    if !screen.trim().is_empty() {
        parts.insert(0, format!("What the terminal showed:\n{}", bounded(screen, 12_000)));
    }
    let mut text = parts.join("\n\n");
    if text.len() > most {
        let mut start = text.len() - most;
        while !text.is_char_boundary(start) {
            start += 1;
        }
        text = format!("[… the start was left out …]\n{}", &text[start..]);
    }
    text
}

/// The context block a harness gets with the next prompt for turns it has not seen, at most about
/// `budget` characters: the newest turns whole, and before them the model's `notes` on the older
/// ones, or a line saying how many were left out. None when there is nothing to give.
pub fn context(chat: &Chat, turns: &[Turn], budget: usize, notes: Option<&str>) -> Option<String> {
    if turns.is_empty() && chat.screen.trim().is_empty() {
        return None;
    }
    let notes = notes.map(str::trim).filter(|notes| !notes.is_empty());
    let room = budget.saturating_sub(notes.map_or(0, |n| n.len().min(budget / 2)));
    let start = newest(turns, room);
    let each = (room / 2).max(1_000);
    let mut parts = Vec::new();
    let names: Vec<&str> = chat.harnesses.iter().map(String::as_str).filter(|n| !n.is_empty()).collect();
    let taken = if names.is_empty() { String::new() } else { format!(" You take it over from {}.", names.join(", then ")) };
    parts.push(format!(
        "[This conversation began earlier, and Yougori, which holds the harnesses, kept it.{taken} The earlier part follows.]"
    ));
    if let Some(notes) = notes {
        parts.push(format!("Notes on the earliest part:\n{}", bounded(notes, budget / 2)));
    } else if start > 0 {
        parts.push(format!("[{start} earlier messages are left out to fit.]"));
    }
    // A terminal harness that gave no turns of its own: what its terminal showed.
    if turns.is_empty() && !chat.screen.trim().is_empty() {
        let screen = chat.screen.trim_start_matches(['\n', '\r']).trim_end();
        parts.push(format!("What the harness's terminal showed:\n{}", bounded(screen, room / 2)));
    }
    parts.extend(turns[start..].iter().map(|turn| written(turn, each)));
    parts.push("[End of the earlier conversation. The user's new message follows.]".into());
    Some(parts.join("\n\n"))
}

/// The prompt with the earlier turns before it.
pub fn with_context(context: &str, prompt: &str) -> String {
    format!("{context}\n\n{prompt}")
}

/// How much of the earlier chat (characters) the request for the model's notes may carry: what
/// the notes model's window holds besides its reply and its instructions, about three characters
/// a token. `own`: the notes come from the harness's own model, whose window is `window`; another
/// model's (a plan's harness has the notes written by this inference's) is not known here, so at
/// most 60,000 characters go to it.
pub fn notes_room(window: u32, max_output: u32, own: bool) -> usize {
    let room = (window as usize).saturating_sub(max_output as usize + 2048) * 3;
    room.clamp(8_000, if own { 300_000 } else { 60_000 })
}

/// Whether the turns need the model's notes to fit: they do not fit whole within `budget`.
pub fn needs_notes(chat: &Chat, turns: &[Turn], budget: usize) -> bool {
    newest(turns, budget) > 0 || (!chat.screen.trim().is_empty() && chat.screen.len() > budget / 2 && turns.is_empty())
}

/// The model's notes on the turns before the newest ones that fit (`turns[..split]`): one chat
/// completion through the relay, at most `max_tokens` long. The words of the failure otherwise.
pub async fn summarize(base: &str, key: &str, model: &str, text: &str, max_tokens: u32) -> Result<String, String> {
    let llm = Llm::new(base, key).map_err(|e| e.to_string())?;
    let messages = vec![
        json!({"role": "system", "content": SUMMARY_PROMPT}),
        json!({"role": "user", "content": format!("The earlier conversation:\n\n{text}")}),
    ];
    let request = Request {
        model,
        messages: &messages,
        tools: &[],
        no_tools: false,
        max_tokens: max_tokens.clamp(256, 2048),
        temperature: Some(0.2),
        top_p: None,
    };
    let cancel = Cancel::new();
    let reply = llm.chat(&request, &cancel, &mut |_| {}).await.map_err(|error| match error {
        crate::native::llm::CallError::Cancelled(_) => "Stopped.".to_owned(),
        crate::native::llm::CallError::Overflow(message) => message,
        crate::native::llm::CallError::Failed { message, .. } => message,
    })?;
    let notes = reply.text.trim().to_owned();
    if notes.is_empty() {
        return Err("The model wrote no notes.".into());
    }
    Ok(notes)
}

/// The chats as /history lists them, and whether the person's computer keeps them.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Listing {
    pub chats: Vec<Summary>,
    /// False when this session's chats stay in this VM only (a launcher from before history).
    pub kept: bool,
}

/// Where chats go: the VM's guest, which hands them to the launcher, or (without the session's
/// token: a scripted run, a test) this process's memory.
pub struct Keeper {
    token: Option<String>,
    base: String,
    http: Option<reqwest::Client>,
    local: Mutex<BTreeMap<String, Chat>>,
}

impl Keeper {
    pub fn from_env() -> Keeper {
        Keeper::new(std::env::var(TOKEN).ok().filter(|token| !token.is_empty()), GUEST)
    }

    pub fn new(token: Option<String>, base: &str) -> Keeper {
        let http = token.as_ref().and_then(|_| {
            reqwest::Client::builder().no_proxy().connect_timeout(Duration::from_secs(5)).timeout(GUEST_WAIT).build().ok()
        });
        Keeper { token, base: base.trim_end_matches('/').to_owned(), http, local: Mutex::new(BTreeMap::new()) }
    }

    /// Only this process's memory.
    #[cfg(test)]
    pub fn memory() -> Keeper {
        Keeper::new(None, GUEST)
    }

    fn remember(&self, chat: &Chat) {
        if let Ok(mut local) = self.local.lock() {
            let newer = local.get(&chat.id).is_none_or(|kept| kept.version <= chat.version);
            if newer {
                local.insert(chat.id.clone(), chat.clone());
            }
        }
    }

    async fn ask(&self, path: &str, body: Value) -> Result<Value, String> {
        self.ask_within(path, body, GUEST_WAIT).await
    }

    async fn ask_within(&self, path: &str, body: Value, wait: Duration) -> Result<Value, String> {
        let (Some(http), Some(token)) = (&self.http, &self.token) else {
            return Err("no guest".into());
        };
        let response = http
            .post(format!("{}{path}", self.base))
            .header(HEADER, token)
            .timeout(wait)
            .json(&body)
            .send()
            .await
            .map_err(|e| format!("The VM did not answer: {e}"))?;
        let status = response.status();
        let value: Value = response.json().await.unwrap_or(Value::Null);
        if !status.is_success() {
            let said = value["error"].as_str().unwrap_or("The VM refused the request.");
            return Err(said.to_owned());
        }
        Ok(value)
    }

    /// Saves the chat. Whether the person's computer keeps it (else it stays in this VM only).
    pub async fn save(&self, chat: &Chat) -> Result<bool, String> {
        let chat = chat.to_save();
        self.remember(&chat);
        if self.token.is_none() {
            return Ok(false);
        }
        let value = self.ask("/history/save", json!({"chat": chat})).await?;
        Ok(value["kept"] == Value::Bool(true))
    }

    /// The chats to list, newest first.
    pub async fn list(&self) -> Result<Listing, String> {
        let mine: Vec<Summary> = match self.local.lock() {
            Ok(local) => local.values().map(Chat::summary).collect(),
            Err(_) => Vec::new(),
        };
        let mut listing = if self.token.is_some() {
            let value = self.ask("/history/list", json!({})).await?;
            let chats: Vec<Summary> = serde_json::from_value(value["chats"].clone()).unwrap_or_default();
            Listing { chats, kept: value["kept"] == Value::Bool(true) }
        } else {
            Listing::default()
        };
        for summary in mine {
            match listing.chats.iter_mut().find(|known| known.id == summary.id) {
                Some(known) if known.updated <= summary.updated => *known = summary,
                Some(_) => {}
                None => listing.chats.push(summary),
            }
        }
        listing.chats.retain(|summary| valid_id(&summary.id));
        listing.chats.sort_by(|a, b| b.updated.cmp(&a.updated));
        Ok(listing)
    }

    /// A plan the person chose (their own subscription), and how to sign in (`vm`, `computer`,
    /// `saved` or `sign-in`): the launcher opens its sites and sends the sign-in into the VM
    /// (vm_guest.py /subscription). The same guest and token as the chats.
    pub async fn subscribe(&self, plan: &str, using: &str) -> Result<(), String> {
        if self.token.is_none() {
            return Err("This Yougori cannot use your plan here. Update Yougori: yougori update".into());
        }
        self.ask_within("/subscription", json!({"plan": plan, "use": using}), Duration::from_secs(90)).await.map(|_| ())
    }

    /// One chat, whole.
    pub async fn open(&self, id: &str) -> Result<Chat, String> {
        if !valid_id(id) {
            return Err("There is no such chat.".into());
        }
        let mine = self.local.lock().ok().and_then(|local| local.get(id).cloned());
        if let Some(chat) = mine {
            return Ok(chat);
        }
        if self.token.is_none() {
            return Err("There is no such chat.".into());
        }
        let value = self.ask("/history/open", json!({"id": id})).await?;
        let chat: Chat = serde_json::from_value(value["chat"].clone()).map_err(|_| "The chat could not be read.".to_owned())?;
        if chat.id != id {
            return Err("The chat could not be read.".into());
        }
        Ok(chat)
    }
}

enum Saving {
    Chat(Box<Chat>),
    Flush(tokio::sync::oneshot::Sender<()>),
}

/// Saves chats one after another in the background, the screens of every harness of the session
/// sending to it: the newest save of a chat goes when several wait. Each save's outcome comes back
/// (whether the person's computer keeps the chat, or why it could not be saved).
#[derive(Clone)]
pub struct Saver {
    tx: tokio::sync::mpsc::UnboundedSender<Saving>,
}

impl Saver {
    pub fn start(keeper: std::sync::Arc<Keeper>) -> (Saver, tokio::sync::mpsc::UnboundedReceiver<Result<bool, String>>) {
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<Saving>();
        let (told, outcomes) = tokio::sync::mpsc::unbounded_channel();
        tokio::spawn(async move {
            while let Some(first) = rx.recv().await {
                let mut waiting = vec![first];
                while let Ok(more) = rx.try_recv() {
                    waiting.push(more);
                }
                // The newest save of each chat, in the order they came; flushes answer after them.
                let mut chats: Vec<Chat> = Vec::new();
                let mut flushes = Vec::new();
                for item in waiting {
                    match item {
                        Saving::Chat(chat) => match chats.iter_mut().find(|kept| kept.id == chat.id) {
                            Some(kept) if kept.version <= chat.version => *kept = *chat,
                            Some(_) => {}
                            None => chats.push(*chat),
                        },
                        Saving::Flush(done) => flushes.push(done),
                    }
                }
                for chat in chats {
                    let _ = told.send(keeper.save(&chat).await);
                }
                for done in flushes {
                    let _ = done.send(());
                }
            }
        });
        (Saver { tx }, outcomes)
    }

    pub fn save(&self, chat: Chat) {
        let _ = self.tx.send(Saving::Chat(Box::new(chat)));
    }

    /// Waits, at most `limit`, until every chat sent before is saved.
    pub async fn flush(&self, limit: Duration) {
        let (done, wait) = tokio::sync::oneshot::channel();
        if self.tx.send(Saving::Flush(done)).is_ok() {
            let _ = tokio::time::timeout(limit, wait).await;
        }
    }
}

/// When a chat was last changed, in words: "just now", "5 min ago", "3 h ago", "yesterday",
/// "4 days ago", or its date.
pub fn ago(updated: u64, at: u64) -> String {
    let seconds = at.saturating_sub(updated);
    match seconds {
        0..=59 => "just now".into(),
        60..=3_599 => format!("{} min ago", seconds / 60),
        3_600..=86_399 => format!("{} h ago", seconds / 3_600),
        86_400..=172_799 => "yesterday".into(),
        172_800..=2_591_999 => format!("{} days ago", seconds / 86_400),
        _ => {
            // Days since 1970 to a calendar date (proleptic Gregorian, UTC).
            let days = (updated / 86_400) as i64;
            let (y, m, d) = civil(days);
            format!("{y}-{m:02}-{d:02}")
        }
    }
}

fn civil(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

/// The hint /history shows for a chat: its harnesses, its messages, when.
pub fn hint(summary: &Summary, at: u64) -> String {
    let harnesses = if summary.harnesses.is_empty() { summary.name.clone() } else { summary.harnesses.join(" → ") };
    let messages = match summary.turns {
        0 => "terminal".to_owned(),
        1 => "1 message".to_owned(),
        n => format!("{n} messages"),
    };
    [harnesses, messages, ago(summary.updated, at)].into_iter().filter(|part| !part.is_empty()).collect::<Vec<_>>().join(" · ")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn chat() -> Chat {
        let mut chat = Chat::new("opencode", "OpenCode", "m");
        chat.user("Fix the parser's crash on empty input\nIt panics.");
        chat.reply("Looking at it.", false);
        chat.tool("Read src/parser.rs");
        chat.tool("Read src/parser.rs");
        chat.tool("cargo test");
        chat.reply("Fixed: `tokens(input).next()?`.", true);
        chat
    }

    #[test]
    fn a_chat_records_prompts_replies_and_tools() {
        let chat = chat();
        assert_eq!(chat.title, "Fix the parser's crash on empty input");
        assert_eq!(chat.turns.len(), 2);
        assert_eq!(chat.turns[1].text, "Looking at it.\n\nFixed: `tokens(input).next()?`.");
        assert_eq!(chat.turns[1].tools, ["Read src/parser.rs", "cargo test"]);
        assert_eq!(chat.harnesses, ["OpenCode"]);
        assert!(valid_id(&chat.id) && !chat.is_empty());
        assert!(Chat::new("x", "X", "m").is_empty());
    }

    #[test]
    fn another_harness_takes_the_chat_over() {
        let mut chat = chat();
        chat.entered("codex", "Codex", "m2");
        chat.user("Now add a test.");
        chat.reply("Added.", false);
        assert_eq!(chat.harnesses, ["OpenCode", "Codex"]);
        assert_eq!((chat.turns[3].role, chat.turns[3].harness.as_str()), (Role::Assistant, "Codex"));
        let given = context(&chat, &chat.turns, 10_000, None).unwrap();
        assert!(given.contains("You take it over from OpenCode, then Codex."), "{given}");
        assert!(given.contains("User: Fix the parser's crash on empty input\nIt panics."), "{given}");
        assert!(given.contains("Assistant (OpenCode): Looking at it."), "{given}");
        assert!(given.contains("(tools it ran: Read src/parser.rs; cargo test)"), "{given}");
        assert!(given.ends_with("[End of the earlier conversation. The user's new message follows.]"));
        assert!(context(&Chat::new("x", "X", "m"), &[], 10_000, None).is_none());
    }

    #[test]
    fn a_long_chat_keeps_its_newest_turns_and_says_what_went() {
        let mut chat = Chat::new("yougori", "Yougori", "m");
        for i in 0..40 {
            chat.user(&format!("question {i} {}", "x".repeat(400)));
            chat.reply(&format!("answer {i} {}", "y".repeat(400)), false);
        }
        assert!(needs_notes(&chat, &chat.turns, 8_000));
        let plain = context(&chat, &chat.turns, 8_000, None).unwrap();
        assert!(plain.len() < 9_000, "{}", plain.len());
        assert!(plain.contains("earlier messages are left out to fit") && plain.contains("answer 39"));
        assert!(!plain.contains("question 0 "));
        let noted = context(&chat, &chat.turns, 8_000, Some("Asked: forty questions.")).unwrap();
        assert!(noted.contains("Notes on the earliest part:\nAsked: forty questions.") && noted.contains("answer 39"));
        assert!(!noted.contains("left out to fit"));
    }

    #[test]
    fn a_terminal_harness_keeps_its_screen() {
        let mut chat = Chat::new("strix", "Strix", "m");
        chat.set_screen("  scanning …\n  found: SQL injection\n\n");
        assert_eq!(chat.title, "Strix session");
        assert!(!chat.is_empty());
        let context = context(&chat, &[], 10_000, None).unwrap();
        assert!(context.contains("What the harness's terminal showed:\n  scanning …\n  found: SQL injection"), "{context}");
    }

    #[test]
    fn a_saved_chat_fits_its_bound() {
        let mut chat = Chat::new("yougori", "Yougori", "m");
        for i in 0..100 {
            chat.user(&format!("{i} {}", "q".repeat(20_000)));
            chat.reply(&"a".repeat(20_000), false);
        }
        let saved = chat.to_save();
        assert!(serde_json::to_vec(&saved).unwrap().len() <= CHAT_BYTES);
        assert!(saved.turns.len() < chat.turns.len() && saved.turns.last() == chat.turns.last());
        // Only the turns that had to go went: one more would not fit.
        let mut more = saved.clone();
        more.turns.insert(0, chat.turns[chat.turns.len() - saved.turns.len() - 1].clone());
        assert!(serde_json::to_vec(&more).unwrap().len() > CHAT_BYTES);
        // A chat within its bound is saved whole.
        let short = Chat::new("yougori", "Yougori", "m");
        assert_eq!(short.to_save(), short);
    }

    #[test]
    fn the_notes_request_fits_the_window_of_the_model_that_writes_them() {
        // The harness's own model: its window, less its reply, about three characters a token.
        assert_eq!(notes_room(32_768, 4_096, true), (32_768 - 4_096 - 2_048) * 3);
        assert_eq!(notes_room(4_096, 4_096, true), 8_000);
        assert_eq!(notes_room(1_048_576, 8_192, true), 300_000);
        // A plan's harness: its window is the plan's, and the notes come from another model.
        assert_eq!(notes_room(200_000, 32_000, false), 60_000);
    }

    #[test]
    fn long_texts_keep_their_start_and_end() {
        let text = format!("start{}end", "é".repeat(5_000));
        let kept = bounded(&text, 1_000);
        assert!(kept.len() <= 1_100 && kept.starts_with("start") && kept.ends_with("end") && kept.contains("left out"));
        assert_eq!(title(&"w".repeat(100)).chars().count(), 73);
    }

    #[test]
    fn times_read_as_words() {
        assert_eq!(ago(1_000, 1_030), "just now");
        assert_eq!(ago(1_000, 1_000 + 300), "5 min ago");
        assert_eq!(ago(1_000, 1_000 + 3 * 3_600), "3 h ago");
        assert_eq!(ago(1_000, 1_000 + 30 * 3_600), "yesterday");
        assert_eq!(ago(1_000, 1_000 + 4 * 86_400), "4 days ago");
        assert_eq!(ago(1_763_164_800, 1_763_164_800 + 60 * 86_400), "2025-11-15");
        let summary = Summary { harnesses: vec!["OpenCode".into(), "Codex".into()], turns: 4, updated: 0, ..Summary::default() };
        assert_eq!(hint(&summary, 120), "OpenCode → Codex · 4 messages · 2 min ago");
    }

    #[tokio::test]
    async fn the_saver_saves_the_newest_and_flushes() {
        let keeper = std::sync::Arc::new(Keeper::memory());
        let (saver, mut outcomes) = Saver::start(keeper.clone());
        let mut chat = chat();
        for version in 1..=3 {
            chat.version = version;
            chat.user(&format!("turn {version}"));
            saver.save(chat.clone());
        }
        saver.flush(Duration::from_secs(5)).await;
        assert_eq!(outcomes.recv().await, Some(Ok(false)));
        let kept = keeper.open(&chat.id).await.unwrap();
        assert_eq!(kept.version, 3);
        assert_eq!(kept.turns.last().unwrap().text, "turn 3");
    }

    #[tokio::test]
    async fn without_a_guest_chats_stay_in_memory() {
        let keeper = Keeper::memory();
        let mut first = chat();
        assert_eq!(keeper.save(&first).await, Ok(false));
        first.version = 2;
        first.user("more");
        keeper.save(&first).await.unwrap();
        let mut older = first.clone();
        older.version = 1;
        older.turns.truncate(1);
        keeper.save(&older).await.unwrap();
        let listing = keeper.list().await.unwrap();
        assert!(!listing.kept);
        assert_eq!(listing.chats.len(), 1);
        assert_eq!(keeper.open(&first.id).await.unwrap().turns.len(), 3, "the newest save wins");
        assert!(keeper.open(&new_id()).await.is_err());
    }
}
