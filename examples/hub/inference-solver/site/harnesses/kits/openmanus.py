"""OpenManus: a general agent that plans and acts step by step with its own tools: Python and a file
editor (its browser comes from the internet at each start, which a VM does not do). It works in the
person's folder.
Each of its steps shows on a row."""
import asyncio
import re
from pathlib import Path

from terminal_ui import screen

NAME = "OpenManus"
ENGINE = "OpenManus Manus agent · Python and file editor"
TAGLINE = "a general agent for everyday tasks"
PLACEHOLDER = "Give OpenManus a task"
WORKING = "OpenManus is working"
STEP = re.compile(r"^Step (\d+): (.*)$")


def config(kit):
    """OpenManus reads config/config.toml beside its source: this session's model, on the relay."""
    import importlib.util
    root = Path(importlib.util.find_spec("app").origin).resolve().parents[1]
    quote = lambda value: '"%s"' % str(value).replace("\\", "\\\\").replace('"', '\\"')
    (root / "config").mkdir(exist_ok=True)
    (root / "config" / "config.toml").write_text(
        "[llm]\nmodel = %s\nbase_url = %s\napi_key = %s\nmax_tokens = %d\ntemperature = 0.0\n\n"
        # Its settings require a Daytona section even though its Daytona sandbox is not used (the VM is).
        "[daytona]\ndaytona_api_key = \"unused\"\n"
        % (quote(kit.model), quote(kit.base_url.rstrip("/") + "/"), quote(kit.api_key), kit.reply), encoding="utf-8")


class Run:
    def __init__(self, kit):
        self.kit = kit
        config(kit)
        import app.config as settings
        # Its workspace is the person's folder, not its own beside the source.
        settings.WORKSPACE_ROOT = kit.workspace
        self.loop = asyncio.new_event_loop()
        # Its browser is an MCP server it fetches with uvx from the internet at every start, which a VM
        # does not do (it waited without end): it is left out; OpenManus keeps its Python and editor.
        import os
        os.environ["OPENMANUS_DISABLE_BROWSER_USE"] = "1"
        self.tools_words = "its own Python and file editor"
        self.facts = []

    async def _turn(self, text):
        from app.agent.manus import Manus
        agent = await Manus.create()
        try:
            result = await agent.run(text)
            said = [m.content for m in getattr(agent.memory, "messages", []) if getattr(m, "role", "") == "assistant" and m.content]
        finally:
            await agent.cleanup()
        for line in str(result or "").splitlines():
            match = STEP.match(line)
            if match:
                tool = re.search(r"cmd `([^`]+)`", match.group(2))
                screen.row("Step %s" % match.group(1), tool.group(1) if tool else " ".join(match.group(2).split())[:100], "done")
        # Its answer is its last message to the person; its steps' outputs are on their rows.
        return said[-1] if said else str(result or "")

    def turn(self, text):
        return self.loop.run_until_complete(self._turn(text))


def start(kit):
    return Run(kit)
