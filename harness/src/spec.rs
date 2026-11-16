//! How to start the chosen harness's engine: `engine.json`, written inside the VM by
//! `runtime.py` (profiles.headless) from the harness, model and setup answers, read here.
//!
//! The per-harness knowledge (config files, environment, the headless command) stays in the
//! Python profiles beside the other harness runtime code; this binary starts what the spec says
//! and speaks the spec's protocol.
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

/// The relay every model request goes through inside the VM.
pub const RELAY: &str = "http://127.0.0.1:8080/v1";
/// Not a credential: the relay outside the VM holds the inference's key.
pub const RELAY_KEY: &str = "solver-local-relay";

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct EngineSpec {
    /// The chosen harness, such as `opencode`, `strix` or `yougori`.
    pub harness: String,
    /// Its display name.
    pub name: String,
    /// `coding` or `security`.
    #[serde(default = "coding")]
    pub category: String,
    pub protocol: Protocol,
    /// The engine's command (not used by `native`).
    #[serde(default)]
    pub argv: Vec<String>,
    /// The engine's whole environment (nothing else is inherited).
    #[serde(default)]
    pub env: BTreeMap<String, String>,
    /// Secret setup answers the engine gets in its environment: engine variable -> answer id. The
    /// value comes from this process's `YOUGORI_SECRET_<ID>` variable (ID upper-cased), so secrets
    /// are never written into the spec, arguments, logs or reports.
    #[serde(default)]
    pub secret_env: BTreeMap<String, String>,
    /// Where the engine works: the workspace.
    pub cwd: PathBuf,
    pub model: String,
    /// This inference's models, for the model switcher.
    #[serde(default)]
    pub models: Vec<String>,
    #[serde(default)]
    pub model_switch: crate::protocol::ModelSwitch,
    /// A command that prints a new spec for another model (the model id is appended), for engines
    /// that restart to switch models: `["python3", "/opt/solver/runtime.py", "engine", "opencode"]`.
    #[serde(default)]
    pub respec: Vec<String>,
    /// Our own engine's primary profile for `native`: `yougori` (builds and edits) or `plan`.
    #[serde(default)]
    pub profile: Option<String>,
    /// MCP servers our own engine connects to (`native`), or passes to an ACP agent.
    #[serde(default)]
    pub mcp: Vec<McpServer>,
    /// Services started before the engine and stopped after it, in order.
    #[serde(default)]
    pub services: Vec<Service>,
    #[serde(default = "context_window")]
    pub context_window: u32,
    #[serde(default = "max_output")]
    pub max_output: u32,
    #[serde(default = "relay")]
    pub base_url: String,
    #[serde(default = "relay_key")]
    pub api_key: String,
    /// The setup answers (no secrets: those are in `env` only).
    #[serde(default)]
    pub answers: Value,
    /// What the VM can reach besides the inference.
    #[serde(default)]
    pub reach: Reach,
    /// Folder for this harness's own state (sessions, logs), inside the VM's home.
    pub state_dir: PathBuf,
    /// What the screen needs to switch harness (/harness), from the launcher; empty in specs
    /// from an older launcher or image.
    #[serde(flatten)]
    pub session: Session,
    /// The client's own sign-in, run in the terminal pane before the engine starts, when the
    /// model is a plan the client is not signed in to yet (`claude auth login`).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub sign_in: Vec<String>,
    /// The model's name for the engine itself, when it differs from `model` (a plan's model:
    /// `sonnet` for `subscription:claude/sonnet`); empty: the engine's own default.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub engine_model: Option<String>,
}

/// The harnesses a session may switch to and what the switch command needs (runtime.py writes
/// them from what the launcher told the VM).
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct Session {
    /// Yougori's own agent first, then the coding and the security harnesses, as listed.
    #[serde(default)]
    pub harnesses: Vec<HarnessChoice>,
    /// The switch command (`runtime.py switch`): the request on stdin, the next spec on stdout.
    #[serde(default)]
    pub switch: Vec<String>,
    /// The base image this VM started on, and its memory in GB.
    #[serde(default)]
    pub vm_base: String,
    #[serde(default)]
    pub vm_memory: u32,
    /// The inference's address, for the command that starts another session.
    #[serde(default)]
    pub server: String,
    /// The chat this session goes on with (a /harness switch into a new VM brought it), by id:
    /// the screen opens it from the history when it starts.
    #[serde(default)]
    pub resume: String,
    /// Each plan's sign-ins the person's computer holds (`claude`, `chatgpt`), which the screen
    /// offers when the person chooses the plan.
    #[serde(default)]
    pub subscriptions: BTreeMap<String, PlanState>,
}

/// What the person's computer holds of a plan's sign-in.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct PlanState {
    /// One Yougori kept from an earlier sign-in in a VM.
    #[serde(default)]
    pub saved: bool,
    /// The computer's own client's.
    #[serde(default)]
    pub computer: bool,
}

/// A plan the person signs in to: its id, its name, the harness whose client signs in, the sign-in
/// file in that client's home, and the sites its client reaches.
pub struct Plan {
    pub id: &'static str,
    pub name: &'static str,
    pub harness: &'static str,
    /// The client's own name, as the screen names its harness.
    pub client: &'static str,
    pub credential: &'static str,
    pub sites: &'static [&'static str],
    /// Whether the sign-in may travel: taken from the person's computer, and kept by Yougori for
    /// the next session. OpenAI documents copying Codex's auth.json to another machine. Anthropic
    /// lets no third party collect, store or pass on Claude credentials: a Claude sign-in completes
    /// in Claude Code's own sign-in inside the VM, and stays there.
    pub transferable: bool,
    /// How its client signs in, as the plan's panel says.
    pub how: &'static str,
}

pub const PLANS: [Plan; 2] = [
    Plan {
        id: "claude",
        name: "Claude",
        harness: "claude",
        client: "Claude Code",
        credential: ".claude/.credentials.json",
        sites: &["anthropic.com", "claude.ai", "claude.com"],
        transferable: false,
        how: "Claude Code's own sign-in, in your browser; it stays in this VM",
    },
    Plan {
        id: "chatgpt",
        name: "ChatGPT",
        harness: "codex",
        client: "Codex",
        credential: ".codex/auth.json",
        sites: &["chatgpt.com", "openai.com"],
        transferable: true,
        how: "Codex gives a code for your browser (device code sign-in must be on in ChatGPT's security settings); Yougori keeps the sign-in",
    },
];

/// The plan and the model's name there, for a plan's model id.
pub fn plan_of(model: &str) -> Option<(&'static Plan, &str)> {
    let rest = model.strip_prefix(SUBSCRIPTION)?;
    let (plan, name) = rest.split_once('/').unwrap_or((rest, "default"));
    PLANS.iter().find(|p| p.id == plan).map(|p| (p, name))
}

/// A model as the screen names it: a plan's as "Claude plan · sonnet", any other as it is.
pub fn shown_model(model: &str) -> String {
    match plan_of(model) {
        Some((plan, "default")) => format!("{} plan", plan.name),
        Some((plan, name)) => format!("{} plan · {name}", plan.name),
        None if model.is_empty() => "no model yet".into(),
        None => model.to_owned(),
    }
}

/// A harness this session may switch to, as the website lists it.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct HarnessChoice {
    pub id: String,
    pub name: String,
    #[serde(default = "coding")]
    pub category: String,
    /// The kind of work it is for, as the website's categories say (agents, data, research...):
    /// what the list names after it. Empty from a launcher before them: the category then.
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub description: String,
    /// Its own questions, as the website describes them (asked with the CLI's meaning).
    #[serde(default)]
    pub questions: Vec<Value>,
    /// The base image it starts on, and the memory it needs in GB.
    #[serde(default)]
    pub base: String,
    #[serde(default)]
    pub memory: u32,
    #[serde(default)]
    pub ready: bool,
    /// Why it cannot start, when it cannot.
    #[serde(default)]
    pub reason: String,
}

impl Session {
    /// Whether this session can switch harness here.
    pub fn switchable(&self) -> bool {
        !self.switch.is_empty() && !self.harnesses.is_empty()
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum Protocol {
    /// Our own agent engine.
    Native,
    /// Agent Client Protocol over the child's stdio (OpenCode, Kilo, Gemini CLI, Qwen Code,
    /// DeepSeek, and our Python bridges).
    Acp,
    /// Codex's app-server JSON-RPC over stdio.
    CodexAppServer,
    /// Claude Code's `--input-format stream-json --output-format stream-json`.
    ClaudeStreamJson,
    /// Pi's `--mode rpc`.
    PiRpc,
    /// The harness's own interactive terminal client (`argv`), shown in a terminal pane inside
    /// the Yougori screen: for harnesses without a headless interface we can drive.
    Terminal,
    /// No model yet (the inference lists none for the person): nothing runs until the person
    /// chooses one with /model, which starts the harness on it through `respec`.
    Waiting,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct McpServer {
    pub name: String,
    /// stdio: the command; empty for HTTP.
    #[serde(default)]
    pub command: Vec<String>,
    #[serde(default)]
    pub env: BTreeMap<String, String>,
    /// Streamable HTTP endpoint, when not stdio.
    #[serde(default)]
    pub url: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Service {
    pub name: String,
    pub argv: Vec<String>,
    #[serde(default)]
    pub env: BTreeMap<String, String>,
    #[serde(default)]
    pub cwd: Option<PathBuf>,
    /// The service is ready once this URL answers (any HTTP status).
    #[serde(default)]
    pub ready_url: Option<String>,
    #[serde(default = "ready_seconds")]
    pub ready_seconds: u64,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Reach {
    /// `inference`, `sites`, `targets` or `full`.
    #[serde(default = "inference")]
    pub mode: String,
    /// Sites (HTTPS) for `sites`.
    #[serde(default)]
    pub sites: Vec<String>,
    /// Hosts or addresses with ports for `targets`, as the person gave them.
    #[serde(default)]
    pub targets: Vec<String>,
}

impl Default for Reach {
    fn default() -> Self {
        Reach { mode: inference(), sites: Vec::new(), targets: Vec::new() }
    }
}

impl Reach {
    /// Whether the harness may fetch web pages at all (webfetch).
    pub fn web(&self) -> bool {
        self.mode != "inference"
    }
}

/// What a model id starts with when it is a plan the person signed in to (their own Claude or
/// ChatGPT subscription, in Claude Code or Codex), not one of this inference's models: the relay
/// never serves it.
pub const SUBSCRIPTION: &str = "subscription:";

/// Whether `model` is a subscription the person signed in to.
pub fn subscribed(model: &str) -> bool {
    model.starts_with(SUBSCRIPTION)
}

fn coding() -> String {
    "coding".into()
}
fn inference() -> String {
    "inference".into()
}
fn context_window() -> u32 {
    32768
}
fn max_output() -> u32 {
    4096
}
fn relay() -> String {
    RELAY.into()
}
fn relay_key() -> String {
    RELAY_KEY.into()
}
fn ready_seconds() -> u64 {
    120
}

/// Secret answers the person typed in the screen (a harness switched to with /harness), by answer
/// id: in this process's memory only, never in the environment, a file or a spec.
fn typed() -> &'static std::sync::Mutex<BTreeMap<String, String>> {
    static TYPED: std::sync::OnceLock<std::sync::Mutex<BTreeMap<String, String>>> = std::sync::OnceLock::new();
    TYPED.get_or_init(Default::default)
}

/// Keeps a secret answer typed in the screen, for the engine it goes to and the masking.
pub fn remember_secret(id: &str, value: &str) {
    if let Ok(mut typed) = typed().lock() {
        typed.insert(id.to_ascii_lowercase(), value.to_owned());
    }
}

/// The value of a secret setup answer: typed in the screen, or from `YOUGORI_SECRET_<ID>`.
pub fn secret(id: &str) -> Option<String> {
    let typed = typed().lock().ok().and_then(|typed| typed.get(&id.to_ascii_lowercase()).cloned());
    typed.or_else(|| std::env::var(format!("YOUGORI_SECRET_{}", id.to_ascii_uppercase())).ok()).filter(|v| !v.is_empty())
}

/// Every secret setup answer this process holds, to mask them in what the screen shows.
pub fn secrets() -> Vec<String> {
    let mut found: Vec<String> = std::env::vars()
        .filter(|(name, value)| name.starts_with("YOUGORI_SECRET_") && value.chars().count() >= 4)
        .map(|(_, value)| value)
        .collect();
    if let Ok(typed) = typed().lock() {
        found.extend(typed.values().filter(|value| value.chars().count() >= 4).cloned());
    }
    found
}

impl EngineSpec {
    /// The engine's environment with its secrets filled in.
    pub fn environment(&self) -> BTreeMap<String, String> {
        let mut env = self.env.clone();
        for (name, id) in &self.secret_env {
            if let Some(value) = secret(id) {
                env.insert(name.clone(), value);
            }
        }
        env
    }

    pub fn load(path: &Path) -> anyhow::Result<Self> {
        let raw = std::fs::read(path).map_err(|e| anyhow::anyhow!("Cannot read the engine spec {}: {e}", path.display()))?;
        let spec: EngineSpec = serde_json::from_slice(&raw).map_err(|e| anyhow::anyhow!("Invalid engine spec: {e}"))?;
        spec.check()?;
        Ok(spec)
    }

    pub fn check(&self) -> anyhow::Result<()> {
        if self.harness.is_empty() || (self.model.is_empty() && self.protocol != Protocol::Waiting) {
            anyhow::bail!("The engine spec names no harness or model.");
        }
        if self.protocol == Protocol::Waiting {
            if !self.model.is_empty() || self.respec.is_empty() {
                anyhow::bail!("A spec waiting for a model has no model yet and a respec to start {} with one.", self.harness);
            }
            return Ok(());
        }
        if self.protocol != Protocol::Native && self.argv.is_empty() {
            anyhow::bail!("The engine spec has no command for {}.", self.harness);
        }
        if self.protocol == Protocol::Native && self.profile.is_none() {
            anyhow::bail!("The engine spec has no profile for our own engine.");
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_minimal_spec_takes_the_defaults() {
        let spec: EngineSpec = serde_json::from_str(
            r#"{"harness":"opencode","name":"OpenCode","protocol":"acp","argv":["opencode","acp"],
                "cwd":"/workspace","model":"m","state_dir":"/home/node/.yougori"}"#,
        )
        .unwrap();
        spec.check().unwrap();
        assert_eq!((spec.context_window, spec.max_output), (32768, 4096));
        assert_eq!(spec.base_url, RELAY);
        assert_eq!(spec.reach.mode, "inference");
        assert!(!spec.reach.web());
        let native: EngineSpec = serde_json::from_str(
            r#"{"harness":"yougori","name":"Yougori","protocol":"native","cwd":"/workspace","model":"m","state_dir":"/s"}"#,
        )
        .unwrap();
        assert!(native.check().is_err(), "native needs a profile");
    }
}
