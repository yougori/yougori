# Inference Solver harness launcher

Every harness starts with one Yougori command. Install or update Yougori, then run, replacing
`https://solver.example` with your website:

```powershell
yougori inference run --server https://solver.example
```

It asks, with Back: which harness (Yougori's own agent first, then the coding harnesses, then the
security ones), which model, that harness's own questions, the folder copied into the VM, what
the VM may reach (a security harness is not asked), and its CPU and memory. Then Start or
Cancel. The first time, before it lists the models, it shows a link and an eight-character code:
sign in on the website, enter the code and approve the harness. The terminal then goes on by
itself. No API key is created or copied. Run the same command next time;
`yougori inference run shannon --server URL` starts the list on Shannon.

Every harness starts inside the Yougori harness: Yougori's own screen in front of the harness's
own engine, in a Yougori Sandbox VM. The VM starts from the Yougori base image, and the launcher
delivers the chosen harness's pack through the VM's private control connection, after checking
its hash. Yougori's own agent needs no pack.

Options answer the questions: `--harness NAME` (or the name after `run`), `--model ID`,
`--set ID=VALUE` for the harness's own questions, `--workspace PATH`,
`--internet inference|sites|targets|full` with `--sites github.com,pypi.org` or
`--targets staging.example.com:443`, `--cpu N` and `--memory GB`. `--yes` asks nothing: it takes
the options given and the defaults, and needs the harness, `--model` when there is more than one
model, and each answer the harness requires. A secret answer, such as a test login's password,
comes only from a `YOUGORI_SECRET_<ID>` environment variable (`YOUGORI_SECRET_LOGIN_PASSWORD`),
never from the command line. Nothing goes after `--`. `yougori inference --help` lists every
option.

## What the VM reaches

Prompts and replies always go to this inference through Yougori's relay, which keeps the API key
outside the VM. Besides that, the VM reaches:

- only this inference, by default;
- sites you choose (`--internet sites`): the harness's HTTPS goes through a proxy in the VM
  (`vm_tunnel.py`) over the owner channel, and the launcher opens a tunnel only to a chosen site
  (or its subdomains) on port 443 at a public address; the VM has no network card;
- targets you name (`--internet targets`): Yougori's filter on this computer holds the VM's network
  card to those hosts and ports alone (a host without a port: each of its ports; a web address: its
  host and port). A name is resolved here at launch, and the VM's DNS answers only the names
  chosen. Other sites at a named host's addresses are refused over HTTPS and HTTP, which name the
  site they ask for; other kinds of connection, such as SSH, reach whatever answers there. A target
  is never this computer, and a name that leads to a private address needs that address named too;
- the whole internet as well (`--internet full`): the public internet, never this computer or its
  network. An older Yougori without the filter says so before the VM starts.

## Security harnesses

Shannon, PentAGI, PentestGPT and HexStrike (and Strix, which is not offered at present) each ask:

- what to test: the practice app, a deliberately vulnerable app Yougori starts inside the VM, or
  web addresses, `host:port` or IPv4 addresses (`--set target=practice` or `--set target=URL`);
- whether you own the target or have written permission to test it: an address starts nothing
  without your yes (`--set permission=yes`);
- what is off limits, how far it may go (look only, test safely, or full exploitation;
  `--set depth=look|safe|full`), a test login, the source code (copied in) and a focus.

The VM reaches only the target and this inference. Yougori does not ask, and it refuses `--sites`,
`--targets` and `--internet sites` or `full` for these harnesses. Each runs its own terminal client
inside the Yougori screen. The report comes back to your folder after you review it: the folder
Yougori asks for, or the source code's when you give it. Without a folder the report shows in the
terminal and is not kept.

## Harnesses for every kind of work

Besides coding and security, Yougori holds harnesses for other kinds of work, in the website's
categories (`site/categories.json`): agent frameworks, IT operations, data analysis, research,
office and browser work, customer service, finance and science. Each runs its own engine on this
inference's model, in the same VM, with the same reach and review as the others.

Most of them are libraries and frameworks with no terminal of their own. They run in Yougori's own
terminal for them, `agent_kit.py`, in the security harnesses' look (`terminal_ui.py`): each one's kit
(`kits/NAME.py`) builds that harness's real engine and gives it the terminal's tools as its own kind
of tool: list, read, write and search files, run a command, and, when the VM reaches the web, search
the web and fetch pages. Each tool call shows on a row as it runs; the answer shows in Markdown.
`/harness`, `/model` and `/new` work as for every harness in the pane. The others bring their own
terminal client and run in the pane as they are.

| Category | Harness | How it runs |
| --- | --- | --- |
| Coding | mini-SWE-agent, Continue (`cn`) | their own terminal |
| Agent frameworks | LangGraph, Deep Agents, CrewAI, smolagents, Strands Agents, Google ADK, Microsoft Agent Framework | Yougori's terminal |
| IT operations | HolmesGPT | its own terminal; it asks before each command |
| Data analysis | Vanna, TaskWeaver, PandasAI | Yougori's terminal |
| Research | GPT Researcher, Open Deep Research, PaperQA2, DeerFlow | Yougori's terminal |
| Office and browser | Browser Use, Agent TARS, Stagehand, Eigent, OpenManus | Yougori's terminal |
| Office and browser | OpenClaw | its own terminal (`openclaw chat`) |
| Customer service | Pipecat, LiveKit Agents | Yougori's terminal, in text |
| Finance | TradingAgents, FinRobot | Yougori's terminal (research, not advice) |
| Science | GeoAgent, ChemCrow, Biomni | Yougori's terminal |

Notes:

- What needs the web: GPT Researcher, Open Deep Research and DeerFlow research the web only when the
  VM reaches it (`--internet full`, or `--internet sites` naming the sites); without it they work from
  the documents in the folder. Browser Use, Stagehand, Agent TARS, TradingAgents and FinRobot need the
  web for their pages or data; all but Agent TARS say so at the start when the VM reaches only the
  inference. ChemCrow needs it to start at all: its chemistry tools fetch their data as they load.
- Offline: the base images hold tiktoken's token tables (cl100k, o200k), which several of these
  libraries would otherwise fetch from the internet on first use.
- Embeddings: this inference serves chat models, not embedding models. PaperQA2 uses its keyword
  (sparse) embeddings and GPT Researcher ranks its sources by shared words (`kits/lexical.py`).
- Python: the images have Python 3.12. PandasAI, ChemCrow and FinRobot declare older Pythons; their
  packs install with `--ignore-requires-python` (PandasAI and FinRobot from a frozen, checked set).
  Biomni and OpenManus also install from frozen sets: Biomni declares none of what it imports, and pip
  does not finish resolving OpenManus's requirements.
- Licences: Biomni runs in commercial mode (only commercially licensed datasets and tools) and its
  data lake is not downloaded. TradingAgents' and FinRobot's market data is Yahoo Finance, whose terms
  are for personal use; commercial use needs a licensed feed (their descriptions say so). PandasAI
  3.0's core imports its enterprise folder (`pandasai/ee`, under PandasAI's Enterprise licence): the
  pack carries Yougori's own stand-in there instead (`licenses/stand-ins/pandasai-ee`), and PandasAI
  runs without its enterprise skills. Every pack's licences, and what was left out of it, are in its
  `tool/THIRD-PARTY-LICENSES.txt` (`pack_licenses.py`); the images' own are in `/usr/share/licenses`
  (harness/NOTICE.md says what and why).
- Not offered: Claude Code (preinstalling it needs Anthropic's Commercial Terms of Service accepted)
  and Strix (the Caido proxy it runs comes with no licence to pass it on). Their packs are not built
  (`packages.json` "blocked").
- Customer service: Pipecat's pipeline and LiveKit's AgentSession run in text; the person writes as a
  customer or caller would. Both answer from the Markdown and text files in the folder.

## History and plans

**History.** The Yougori screen records each chat as it goes (`harness/src/history.rs`): the
person's prompts, the harness's replies and the titles of the tools it ran. Yougori's own clients
in the terminal pane (`agent_kit.py`, through `terminal_ui.py`) tell it their turns with OSC 7718
(`turn;user|assistant;BASE64`, `tool;BASE64`); for any other client the pane keeps what its screen
showed once the person typed to it. After every turn, and before the screen ends or switches, the
screen saves the chat to the VM's guest (`/history/save`, with the session's switch token). The guest
hands it to the launcher in `/poll` (`history`), and the launcher keeps it on the person's
computer in the Yougori harness's private folder for the inference, beside its key:
`history/ID.json` and a list, `index.json` (`vm_host.keep_chat`). The newest version of a chat wins. At `/start` the launcher says
it keeps chats (`history`, with `resume` when a switch into a new VM brought a chat), then sends the
list (`/history/index`). `/history` lists it; opening a chat this VM does not hold asks the
launcher (`/poll` `history_open`, answered with `/history/body`). The chat a `/harness` switch into
a new VM was in goes on there: the switch request names it (`chat`), and the next session opens it.

A harness that takes a chat over gets the turns it has not seen with the next prompt
(`history::context`). The newest turns go whole, within a quarter of its window. When older turns
do not fit, the inference's model writes notes on them first (`history::summarize`); if it cannot,
they are left out with a line that says so. Yougori's own clients take the chat from a file the
screen writes before they start (`YOUGORI_CONTEXT_FILE`, read once by `terminal_ui.earlier_chat`).
A launcher or image from before this keeps the chats in the VM only, and `/history` says so.

**Plans.** Codex can run on the person's own ChatGPT subscription, and Claude Code on their Claude
subscription once Claude Code is offered (it is not at present: harness/NOTICE.md)
(`profiles.SUBSCRIPTIONS`). Their model ids start with `subscription:` (`subscription:chatgpt/default`)
and the relay never serves them. Choosing one in `/model` asks how to sign in. For ChatGPT: the
sign-in already in this VM, this computer's own Codex's (`auth.json`, which OpenAI documents copying
to another machine), the one Yougori kept from an earlier session, or signing in. For Claude, only
Claude Code's own sign-in in this VM (or the one it already made there): Anthropic lets no third
party collect, store or pass on Claude credentials, so the launcher never reads, keeps or sends one
(`vm_host.TRANSFERABLE`, `vm_guest.TRANSFERABLE`). The screen asks the guest
(`/subscription`, with the switch token). The launcher decides the plan's sites itself
(`vm_host.SUBSCRIPTION_SITES`: `anthropic.com`, `claude.ai` and `claude.com`, or `chatgpt.com` and
`openai.com`) and opens the VM's proxy for chosen sites to them, starting it when the session had
none. For ChatGPT it sends the chosen sign-in with them (`/subscription/granted`), and the guest
writes it into Codex's own file. The engine then restarts on the plan through the proxy, with none
of the relay's settings. When no sign-in is there yet, the screen first runs the client's own
sign-in in its terminal pane (`claude auth login`, `codex login --device-auth`, which needs device
code sign-in turned on in the person's ChatGPT security settings); Esc there goes back to the model
before. A ChatGPT sign-in made or renewed in the VM goes back in `/poll` (`credentials`). The
launcher keeps it in the inference's private folder (`subscriptions/chatgpt.json`) and, when it came
from this computer's own Codex, writes the renewed one back there while that file is still as it was
given, so both stay signed in. A Claude sign-in stays in the VM it was made in.

## The source setup

For a source installation, install Python 3.12 or newer and Yougori with Sandbox VM support,
extract this folder and run:

```
python solver.py list
python solver.py models pi
python solver.py run yougori --harness pi --workspace /path/to/project
python solver.py run pi --workspace /path/to/project
```

Replace `pi` with the harness you downloaded. The website address is already in
`connection.json`. `run yougori --harness NAME` starts NAME inside the Yougori harness, as
`yougori inference run` does; `--answers-file FILE` gives its setup answers as one JSON object
(never secrets: those come only from `YOUGORI_SECRET_<ID>` variables), and without `--harness` it
starts Yougori's own agent. `run NAME` starts the harness from its own VM image, as launchers from
before the Yougori harness do, and passes the tool arguments after `--`. `install`, `login` and
`check` prepare the image, sign in again and check that the harness can start. The macOS VM backend
is not supported by this launcher.

The reach options are the same. A security harness takes no `--sites` and no `--internet sites`
or `full`. Inside the Yougori harness its target is the target answer: the practice app needs only
the inference, and `--targets` may name only the answer's hosts. Started alone it has no target
question, so `--internet targets --targets HOST:PORT` names its target.

First launch generates its own credential and shows an eight-character code. Open the printed
website link; sign in, enter the code and approve the named harness. The same terminal continues
automatically. Each server and harness has its own credential, saved in an owner-only local folder.
Use `login` to reconnect. Codes expire after ten minutes; denial and revocation refuse access. You
can revoke a connection from the website's API keys. The server stores only credential and code
hashes. Keys are never in downloads, image layers, command arguments or harness environments. A
harness gets only a nonsecret local relay token.

The selected workspace is staged as a copy in the VM; review and promote approved changes with
Yougori before they reach the original folder. Omit `--workspace` for an empty disposable
workspace. Session home directories are ephemeral. No host home, SSH agent, provider account or
Docker socket is shared. When a session ends its VM stops and leaves Yougori's list: the copy is
removed unless the harness changed the project folder, which stays for review. A session that ended
without this (a closed terminal) is tidied by the next run.

`models` lists the server's current authenticated model catalog. Select with `--model MODEL_ID`, or
use the interactive picker. There is no external-provider fallback. Client context/output budgets
are configuration defaults, not discovered model capabilities; use a model that supports the task's
context and tool calls.

## The Windows install script

Older links to `/install/NAME.ps1` still work. That script installs a private, checksum-pinned
Python 3.14.10 runtime and a `solver-NAME` command, which starts NAME from its own VM image:

```powershell
powershell -NoProfile -c "irm 'https://solver.example/install/codex.ps1' | iex"
solver-codex
solver-codex models
solver-codex login
solver-codex --workspace C:\path\to\project
```

The installer checks the source ZIP's SHA-256, rejects redirects and unsafe ZIP paths, and
activates the new command after validating it. Re-run it to update. An existing command connected
to a different server is preserved; the additional server gets a command with a short suffix. The
install directory is normally `%LOCALAPPDATA%\InferenceSolver\cli`. `INFERENCE_SOLVER_HOME`
selects another install directory; `INFERENCE_SOLVER_NO_PATH=1` leaves persistent PATH unchanged
(portable mode); automated packaging can set `INFERENCE_SOLVER_NO_LAUNCH=1` to skip the initial
launch. These checks provide download integrity, not an independent publisher signature.

## The boundary

The boundary is a Yougori Sandbox VM without host folder mounts. The VM's loopback API exchanges
allowlisted inference requests through an owner-only Yougori publication. The host relay forwards
only model API paths to the fixed service origin; it refuses redirects, proxy CONNECT, arbitrary
URLs and account/admin routes. Its real service key stays outside the guest. Beyond the sites,
targets or whole internet allowed for a session, external browsing, git fetch, package downloads
and telemetry are unavailable at runtime. Prepare dependencies before building the VM images.

The official tools are installed into the images, not into your normal CLI profiles. The
multi-service adapters patch pinned upstream source to use the single VM and fixed model relay;
they are not unmodified vendor distributions. Licenses and notices remain with the packaged
sources. A harness started from its own image runs in a real PTY with its chromatic terminal
accents remapped to blue; neutral text, backgrounds, input, resize events and terminal controls
are preserved.

Responses and Messages adapters support stateless text and function tools. Their SSE output is
buffered until the upstream completion ends. Native reasoning, media, hosted provider tools and
stored responses are not emulated. Unsupported features fail with an error. A compatible model
must still support tool calling. Gemini's generateContent adapter has the same text/function and
buffering limits. Codex gets a custom catalog containing only the models available to your key.
The Gemini and Claude clients may still display their own vendor model shortcuts; selecting them
cannot reach that vendor and this server refuses unknown model IDs.

## Harness notes

Grok's pinned executable supports Linux amd64 guests only. Top-level tools and Git revisions are
pinned; transitive package dependencies are resolved when the images are built. A profile is not a
claim that every upstream release was tested on every host; only the real-runtime results recorded
in the template's tests/evidence apply.

Aider, Open Interpreter, SWE-agent and Plandex run inside the Yougori harness through our ACP
bridges (`bridges/`), each driving the harness's real engine. SWE-agent asks what it should fix
(`--set issue="..."`) and works in a Git repository: a selected folder needs its `.git` directory,
and without one it gets an empty disposable Git repository. Existing edits are preserved; the
launcher does not reset the project. Started alone, SWE-agent asks for its task, or takes
`-- --problem_statement.text "task"`. Aider keeps its own files (its chat and input history and its
repo map's tags cache) in its home in the VM, so the review lists only what it changed.

Started alone, Open Interpreter accepts `-- --print "prompt"`, and DeepSeek runs one task with
`-- --print "task"`. PentestGPT runs only in its own interactive `pentestgpt-legacy` client, with
reasoning, parsing and generation all set to the selected server model; it is not its separate
autonomous CTF/Docker deployment.

HexStrike includes its pinned HTTP/MCP server and a terminal companion that uses only the selected
server model. Optional external scanners are reported as unavailable when absent. Plandex includes
its real CLI, local server and PostgreSQL. All agent roles use the selected model. Its context
budget follows the selected model's window as the server lists it (32,768 tokens when the server
does not say, at most 131,072) and must be at least 16,384; choose a model that supports it.

Strix uses the pinned agent SDK's local execution client inside the outer VM, with its Caido
sidecar on loopback. No second container runtime is involved. PentAGI includes PostgreSQL/pgvector,
its server and a terminal client; its pinned execution adapter runs commands and transfers files
inside the same outer VM, with one flow per session. Each of its agents asks at most the selected
model's reply budget (4,096 tokens, less for a small window), not PentAGI's own 16,384.

Shannon's real worker and private Temporal service run inside the VM. Inside the Yougori harness it
scans each target in turn, then stays open. Started alone it opens a `shannon>` prompt: `check` for
model validation, `shell` to start a service inside the VM, `scan URL` to run a task against that
service, `logs` for worker details, and `exit` to stop the VM; `solver-shannon -- --validate-model`
checks only the chosen model and exits. Canonical reports are written under `.shannon/runs` in the
staged workspace. Shannon uses the local chat-completions contract: no provider storage/reasoning
extensions, a 128-token connection check, and at most 4,096 output tokens for a task.

## Images and availability

Large packages are checksum-verified. The launcher downloads them from this website, transfers
them through the private VM connection, and starts the tool only after verification and bounded
extraction. This does not grant external guest access. Publisher builds use
`scripts/build-vm-images.py yougori --output DIR --binary PATH` for the Yougori harness's base
images and packs, and `scripts/build-vm-images.py NAME... --output DIR --split-tools` for each
harness's own image, which launchers from before the Yougori harness start. The website lists a
harness as ready inside the Yougori harness only when the Yougori images list its pack at the
pinned version and the pack and its base image are published whole. The CLI checks this and the
local VM runtime before sign-in. An administrator must publish missing images; a source profile
alone is not ready.

Sources: [OpenCode](https://opencode.ai/docs/providers/),
[Codex](https://developers.openai.com/codex/config-file/config-reference),
[Claude](https://code.claude.com/docs/en/llm-gateway),
[Pi](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/models.md),
[Qwen](https://qwenlm.github.io/qwen-code-docs/en/users/configuration/auth/),
[Aider](https://aider.chat/docs/config/options.html),
[Cline](https://docs.cline.bot/cli/cli-reference),
[Goose](https://goose-docs.ai/docs/getting-started/providers/).
