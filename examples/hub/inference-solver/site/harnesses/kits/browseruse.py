"""Browser Use: a browser agent. Each task drives a fresh Chromium in the VM, headless, with
Browser Use 1.0's own agent (it writes Python against the browser's DevTools protocol, step by
step), until it can give the answer. The browser reaches only the sites the VM reaches; files it
saves go to the folder's browser-use/."""
import asyncio
import shutil
from pathlib import Path

from terminal_ui import screen

NAME = "Browser Use"
ENGINE = "Browser Use 1.0 agent · a headless Chromium in this VM"
TAGLINE = "browser tasks done by an agent"
PLACEHOLDER = "Tell the browser agent what to do on the web"
WORKING = "Browsing"
QUIET_ENV = {"ANONYMIZED_TELEMETRY": "false", "BROWSER_USE_SETUP_LOGGING": "false", "PYDANTIC_AI_NO_BANNER": "1"}
CHROMIUM = ("/usr/lib/chromium/chromium", "/opt/solver/tool/chromium/chromium")


class Run:
    def __init__(self, kit):
        from browser_use import ChatOpenAI
        self.kit = kit
        # Browser Use 1.0's chat classes take the model, key, address and temperature only.
        self.llm = ChatOpenAI(model=kit.model, base_url=kit.base_url, api_key=kit.api_key, temperature=0.2)
        self.chromium = next((path for path in CHROMIUM if shutil.which(path) or __import__("os").path.exists(path)), None)
        if not self.chromium:
            raise ValueError("Chromium is not in this VM.")
        self.past = []
        self.tools_words = 'a headless Chromium it drives'
        self.facts = [("Browser", "Chromium, headless, in this VM")]
        if not kit.web:
            self.hello = "This VM reaches only the inference, so the browser can open no site. Start Yougori with --internet full."

    async def _turn(self, text):
        from browser_use import Agent, Browser
        task = text if not self.past else "Earlier tasks and their results:\n%s\n\nNow: %s" % (
            "\n".join("- %s → %s" % pair for pair in self.past[-4:]), text)
        browser = Browser("chromium", headless=True, executable_path=self.chromium)
        # Its working files stay in its home; what it downloads comes to the folder after the task.
        agent = Agent(task, llm=self.llm, browser=browser, workspace=str(Path.home() / ".yougori" / "browser-use"),
                      use_vision=False, judge=False)
        with screen.step("Web", "Chromium works on the task") as step:
            result = await agent.run(max_steps=30, timeout=900)
            step.note = "%s steps" % getattr(result, "steps", "?")
            if getattr(result, "error", None):
                step.fail(str(result.error)[:300])
        print(repr(result))  # to the log
        for path in getattr(result, "downloads", None) or []:
            target = self.kit.workspace / "downloads" / Path(path).name
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(path, target)
            screen.row("File", "downloads/" + target.name, "done")
        answer = (getattr(result, "text", "") or "").strip() or (result.final_result() or "")
        if getattr(result, "status", "completed") != "completed" and not answer:
            answer = "The browser agent stopped (%s): %s" % (getattr(result, "status", "?"), getattr(result, "error", "") or "no answer")
        self.past.append((text[:200], answer[:300]))
        return answer

    def turn(self, text):
        return asyncio.run(self._turn(text))


def start(kit):
    return Run(kit)
