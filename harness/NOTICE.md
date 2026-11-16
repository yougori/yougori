# Notices

The Yougori harness is AGPL-3.0-only. This file lists the outside work it follows or calls, with
each licence. No prompt, tool text or source code of another harness is copied into it.

## Our own agent and the screen (src/native, src/ui)

Written for this crate. The agent's prompts (`src/native/profiles/*.md`, the summary and `/init`
prompts), its tool descriptions and the screen's words are our own. The agent speaks MCP to the
servers a spec names, from the protocol's published message formats (method and field names,
which are needed for interoperability). The crates the binary is built from (`Cargo.lock`) are
used under their own licences.

## Engine adapters (src/engines: acp, codex, claude, pi, process, services)

No source code, prompt or user-facing text was copied into the adapters. They speak the
following protocols, implemented from their published message formats (field names, method names
and enum values, which are needed for interoperability):

- **Agent Client Protocol** (protocol version 1), schema from `@agentclientprotocol/sdk` 0.14.1,
  0.16.1, 0.21.0 and 1.4.0 (`schema/schema.json`). Apache License 2.0, Copyright Zed Industries,
  Inc. and contributors. Used by `src/engines/acp.rs`, `src/engines/acp/terminals.rs` and
  `examples/hub/inference-solver/site/harnesses/bridges/acp.py`.
- **Codex app-server protocol** at Codex 0.166.0, from the JSON schema the pinned binary prints
  (`codex app-server generate-json-schema`). Codex is Apache License 2.0, Copyright 2025 OpenAI.
  Used by `src/engines/codex.rs`.
- **Claude Agent SDK for Python** (github.com/anthropics/claude-agent-sdk-python, commit
  3cf3446), the control message shapes of Claude Code's stream-json mode (`initialize`,
  `can_use_tool`, `interrupt`, `set_model`, `set_permission_mode`). MIT License, Copyright (c)
  2025 Anthropic, PBC. Used by `src/engines/claude.rs`. Claude Code itself is proprietary: none
  of its text is part of the harness; it runs as its own program.
- **Pi RPC mode** at 0.73.1 (`packages/coding-agent/docs/rpc.md`). MIT License, Copyright (c)
  2025 Mario Zechner. Used by `src/engines/pi.rs`.

The harnesses these adapters start (OpenCode, Kilo, Gemini CLI, Qwen Code, DeepSeek dsh, Goose,
OpenHands, Cline, Grok, Codex, Pi) are separate programs installed in the Sandbox VM under their own
licences; the adapters exchange messages with them over pipes. The security harnesses (Shannon,
PentAGI, PentestGPT, HexStrike) run their own terminal clients in the screen's terminal pane
(`src/ui/pane.rs`), also as separate programs under their own licences.

Two harnesses are not offered, and their packs are not built (`packages.json` "blocked"); the
adapters and profiles stay for when they can be:

- **Claude Code** is proprietary. Anthropic lets a product preinstall it only once the product's
  operator has accepted Anthropic's Commercial Terms of Service, the binary is unmodified, and each
  person signs in with their own credentials (code.claude.com/docs/en/legal-and-compliance).
- **Strix** (Apache-2.0) runs Caido's proxy (`caido-cli`) and Caido's Python SDK, which come with no
  licence to pass them on: offering Strix needs Caido Labs' permission.

## Plans (a person's own subscription, `src/spec.rs` PLANS)

- **ChatGPT plan, in Codex:** Codex's own sign-in (`codex login --device-auth`), in the VM. The
  sign-in may also come from the person's own computer, and Yougori keeps a renewed one for the
  next session, as OpenAI documents copying Codex's `auth.json` to another machine.
- **Claude plan, in Claude Code:** only Claude Code's own sign-in, in the VM; Yougori never takes,
  keeps or passes on a Claude sign-in (Anthropic lets no third party collect, store or intermediate
  Claude credentials). Claude Code is not offered at present (above), so neither is the plan.

## Yougori's terminal for agent frameworks (examples/hub/inference-solver/site/harnesses: agent_kit.py, kits/)

`agent_kit.py` and the kits are our own files, part of the inference-solver template (MIT). No source
code, prompt or user-facing text of the frameworks is copied into them: each kit builds its
framework's agent through the framework's public API, in the framework's own venv inside the Sandbox
VM, so while it runs the kit and the framework are one program. The frameworks, at the pins in
`packages.json`: LangGraph, Deep Agents, CrewAI, Microsoft Agent Framework, Vanna, GPT Researcher,
Open Deep Research, TaskWeaver, PandasAI (its MIT core), GeoAgent, ChemCrow, DeerFlow, OpenManus,
FinRobot, Browser Use and Stagehand (MIT); smolagents, Strands Agents, Google ADK, PaperQA2,
TradingAgents, Biomni, CAMEL (Eigent's engine), LiveKit Agents and Agent TARS (Apache-2.0); Pipecat
(BSD-2-Clause).

- Some of the libraries those processes load are copyleft: PyMuPDF (AGPL-3.0, or Artifex's commercial
  licence) in GPT Researcher and Open Deep Research; html2text (GPL-3.0-or-later) in PaperQA2, ChemCrow
  and OpenManus; backtrader (GPL-3.0-or-later) in FinRobot; Unidecode (GPL-2.0-or-later) in ChemCrow.
  While such a kit runs, it is a program covered by that licence: the kits' MIT licence is compatible
  with it, every part of that program reaches people as source (Python) or with the address of its
  complete source (PyMuPDF's compiled MuPDF), and the packs give them under those licences' terms.
- LiveKit Agents' local models (`livekit-local-inference`) are under LiveKit's model licence, which
  lets them be passed on and used with LiveKit Agents only, as the LiveKit kit uses them.
- PandasAI's core imports its `pandasai/ee` folder, which is under PandasAI's Enterprise licence (paid
  seats for production use). Yougori's PandasAI pack does not carry it: our own stand-in
  (`licenses/stand-ins/pandasai-ee`, MIT, no PandasAI code) takes its place, and PandasAI runs
  without its enterprise "skills".

The new terminal clients run as their own programs in the terminal pane: mini-SWE-agent and OpenClaw
(MIT), HolmesGPT and Continue (Apache-2.0).

## What the VM images pass on (examples/hub/inference-solver/site/harnesses: Containerfile.yougori, pack_licenses.py, licenses/)

- Every base image carries `/usr/share/licenses`: each Debian package's copyright record with the
  list of packages, versions and source packages (`licenses/debian-licenses.sh`, written before the
  build removes `/usr/share/doc`), Node.js's licence, this binary's AGPL-3.0 text with the notices of
  its crates (`licenses/yougori-harness-crates.txt`, from the repository's rendered notices by
  `scripts/harness-notices.py`), the runtime files' MIT licence, and Yougori's written offer of the
  complete source of every copyleft part (`licenses/README.txt`).
- Every pack carries `tool/THIRD-PARTY-LICENSES.txt` (`pack_licenses.py`): each Python distribution,
  Node package and Go module in it with its licence, its authors and its licence text (the standard
  text of the licence it declares when it carries none), the release executables' licences and
  notices (Goose, Grok with Grok Build's third-party notices, Temporal, Typst, Chromium), where the
  source of each copyleft part is, and the changes Yougori made: Shannon's source changes (AGPL-3.0
  asks them to be dated), and what was left out because it may not be passed on or carries no licence
  and is unused (SAP's two packages in Cline, Laminar's closed proxy in OpenHands, `git-python` in
  Open Interpreter, the WebLINX benchmark and NVIDIA's empty CUDA meta-package in OpenManus). A pack
  with a package whose licence cannot be named is not built.

## Coding bridges (examples/hub/inference-solver/site/harnesses/bridges: aider_acp, interpreter_acp, swe_acp, plandex_acp, support_acp)

No source code, prompt or user-facing text of these harnesses is copied into the bridges. Each
bridge calls its harness's public Python API, or runs its own CLI and server, in the harness's own
venv inside the Sandbox VM; the text the screen shows (replies, questions, command and mode
descriptions) is the harness's own, passed through at run time. The bridges are part of the
inference-solver template (MIT, `examples/hub/inference-solver/LICENSE`).

- **Aider** 0.86.2 (`aider-chat`, github.com/Aider-AI/aider). Apache License 2.0. `aider_acp.py`
  imports it in its process (`aider.main`, `Coder`, `InputOutput`, `Commands`), gives it an
  `InputOutput` subclass, observes `Model.send_completion`'s stream and, for the session, runs
  the shell commands aider asks for in place of its `run_cmd`.
- **Open Interpreter** 0.4.3 (`open-interpreter`). GNU Affero General Public License v3, checked
  at the pin: the wheel's `LICENSE` is the AGPL-3.0 text (sha256 `490a8e3e…c812`) and its metadata
  says AGPL. `interpreter_acp.py` is our own file; it imports Open Interpreter as a library, so
  while it runs, the bridge and Open Interpreter are one program, and that program is covered by
  the AGPL-3.0 (the bridge's own MIT licence is compatible with it). What this means: Open
  Interpreter is used unmodified; the bridge reaches people only as its source file, beside Open
  Interpreter's published package in the harness pack, so whoever receives the pack has the
  complete source of the program it runs (Open Interpreter's at the pin in `packages.json`, and
  the bridge itself) under the AGPL-3.0's terms; any future change to Open Interpreter itself
  would have to be offered under the AGPL-3.0, including to people using it over a network
  (section 13). Shannon, the other AGPL harness, stays a separate program.
- **SWE-agent** at commit 3ea751c (sweagent 1.1.0) with SWE-ReX 1.4.0. MIT License, Copyright (c)
  2024 John Yang, Carlos E. Jimenez, Alexander Wettig, Shunyu Yao, Karthik Narasimhan, Ofir
  Press. `swe_acp.py` uses its run API (`RunSingle`, `BasicCLI`, `AbstractAgentHook`).
- **Plandex** 2.2.1 (commit e2d77207). MIT License, Copyright (c) 2025 PlandexAI Inc.
  `plandex_acp.py` runs Plandex's CLI and server as separate programs and reads its server's HTTP
  API as its CLI does (the plan stream and its message types, plan state, diffs, branches), from
  their published source; `plandex_cli.py`'s model proxy is reused.
