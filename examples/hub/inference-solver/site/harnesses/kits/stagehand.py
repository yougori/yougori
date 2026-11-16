"""Stagehand: Browserbase's browser automation, on a local headless Chromium in the VM. Stagehand's own
engine (it runs in the browser) carries out each step: open a page, act on it in words ("click the
sign-in button"), observe what can be done there, extract what a page says. Its model is this
inference's, through Stagehand's custom-model callback; a planner on the same model turns the
person's task into those steps, each shown on a row."""
import asyncio
import json
import shutil
import urllib.request
from pathlib import Path

from terminal_ui import screen

NAME = "Stagehand"
ENGINE = "Stagehand · act, observe and extract on a local Chromium"
TAGLINE = "browser automation in words"
PLACEHOLDER = "Tell Stagehand what to do on the web"
WORKING = "Browsing"
CHROMIUM = ("/usr/lib/chromium/chromium", "/opt/solver/tool/chromium/chromium", "/usr/bin/chromium")
STEPS = 16
PLAN = [
    {"name": "open_page", "description": "Open a web address in the browser.",
     "parameters": {"type": "object", "properties": {"url": {"type": "string", "description": "The full address"}},
                    "required": ["url"]}},
    {"name": "act", "description": "Do one thing on the open page, said in words: click, type, choose, scroll.",
     "parameters": {"type": "object", "properties": {"action": {"type": "string", "description": "The action, such as: click the Sign in link"}},
                    "required": ["action"]}},
    {"name": "observe", "description": "List what can be done on the open page that bears on an aim.",
     "parameters": {"type": "object", "properties": {"aim": {"type": "string"}}, "required": ["aim"]}},
    {"name": "extract", "description": "Read what the open page says about something, as text.",
     "parameters": {"type": "object", "properties": {"what": {"type": "string"}}, "required": ["what"]}},
    {"name": "links", "description": "List the open page's address, title and links (each link's text and address).",
     "parameters": {"type": "object", "properties": {}}},
]
# The open page's links, read in the page itself.
LINKS = ("Array.from(document.links).slice(0, 80).map(a => ({text: (a.innerText || '').trim().slice(0, 120), "
         "href: a.href}))")


def complete(kit, body):
    """One chat completion from the relay (the VM's loopback, never a proxy)."""
    request = urllib.request.Request(kit.base_url.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Authorization": "Bearer " + kit.api_key, "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=600) as response:
        return json.loads(response.read())["choices"][0]["message"]


def data(result, most):
    """What a Stagehand step gave, without its metadata (cache, usage)."""
    value = result.model_dump() if hasattr(result, "model_dump") else result
    if isinstance(value, dict) and "data" in value:
        value = value["data"]
    return json.dumps(value, ensure_ascii=False, default=str)[:most]


def blocks(content):
    return content if isinstance(content, list) else [content]


def to_openai(params):
    """Stagehand's messages as chat-completions messages: text, its tool calls and their results.
    Images are left out: this inference's models read text."""
    messages = [{"role": "system", "content": params["system_prompt"]}] if params.get("system_prompt") else []
    for message in params.get("messages", []):
        text, calls, results = [], [], []
        for block in blocks(message.get("content")):
            kind = block.get("type")
            if kind == "text":
                text.append(block.get("text", ""))
            elif kind == "tool_use":
                calls.append({"id": block["id"], "type": "function",
                              "function": {"name": block["name"], "arguments": json.dumps(block.get("input") or {})}})
            elif kind == "tool_result":
                words = "".join(part.get("text", "") for part in block.get("content") or [] if isinstance(part, dict))
                results.append({"role": "tool", "tool_call_id": block["tool_use_id"],
                                "content": words or json.dumps(block.get("structured_content") or {})})
        if message.get("role") == "assistant":
            messages.append({"role": "assistant", "content": "\n".join(text) or None, **({"tool_calls": calls} if calls else {})})
        else:
            messages.extend(results)
            if text:
                messages.append({"role": "user", "content": "\n".join(text)})
    return messages


class Run:
    def __init__(self, kit):
        self.kit = kit
        self.chromium = next((path for path in CHROMIUM if shutil.which(path) or Path(path).exists()), None)
        if not self.chromium:
            raise ValueError("Chromium is not in this VM.")
        self.loop = asyncio.new_event_loop()
        self.stagehand = None
        self.history = []
        self.tools_words = "Stagehand's act, observe and extract on a headless Chromium"
        self.facts = [("Browser", "Chromium, headless, in this VM, with Stagehand's engine")]
        if not kit.web:
            self.hello = "This VM reaches only the inference, so the browser can open no site. Start Yougori with --internet full."

    async def generate(self, params):
        """Stagehand's custom-model callback: its request on this inference's model."""
        if hasattr(params, "model_dump"):  # the SDK hands over its pydantic models
            params = params.model_dump(by_alias=True, exclude_none=True)
        body = {"model": self.kit.model, "messages": to_openai(params), "max_tokens": self.kit.reply,
                "temperature": params.get("temperature", 0.1)}
        if params.get("stop_sequences"):
            body["stop"] = params["stop_sequences"][:4]
        shape = params.get("response_format") or {}
        if shape.get("type") == "json_schema":
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": shape.get("name", "result"), "schema": shape["schema"]}}
        if params.get("tools"):
            body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                                "parameters": t.get("input_schema") or {"type": "object"}}}
                             for t in params["tools"]]
        reply = await asyncio.to_thread(complete, self.kit, body)
        content = [{"type": "text", "text": reply["content"]}] if reply.get("content") else []
        for call in reply.get("tool_calls") or []:
            content.append({"type": "tool_use", "id": call["id"], "name": call["function"]["name"],
                            "input": json.loads(call["function"].get("arguments") or "{}")})
        if shape.get("type") == "json_schema":
            words = (reply.get("content") or "{}").strip()
            if words.startswith("```"):
                words = words.strip("`").split("\n", 1)[-1]
            return {"role": "assistant", "content": content or [{"type": "text", "text": words}], "output_format": "json_schema",
                    "structured_content": json.loads(words)}
        return {"role": "assistant", "content": content or [{"type": "text", "text": ""}], "output_format": "text"}

    async def browser(self):
        if self.stagehand is None:
            from stagehand import Stagehand, local_browser
            # Chromium's own sandbox cannot start for the VM's user (it needs namespaces the VM does not
            # give); the VM is the boundary, as for Browser Use.
            browser = await local_browser.launch(executable_path=self.chromium, headless=True, chromium_sandbox=False,
                                                 args=["--disable-dev-shm-usage"],
                                                 user_data_dir=str(Path.home() / ".yougori" / "stagehand-profile"))
            self.stagehand = await Stagehand.create(browser=browser, model=self.generate)
        return self.stagehand

    async def step(self, name, arguments):
        stagehand = await self.browser()
        if name == "open_page":
            page = await stagehand.browser.context.active_page() or await stagehand.browser.context.new_page()
            await page.goto(arguments["url"], wait_until="domcontentloaded")
            return "Opened %s." % arguments["url"]
        if name == "links":
            page = await stagehand.browser.context.active_page()
            if page is None:
                return "No page is open: open one first."
            return json.dumps({"url": await page.url(), "title": await page.title(), "links": await page.evaluate(LINKS)},
                              ensure_ascii=False)[:8000]
        if name == "act":
            return data(await stagehand.act(arguments["action"]), 4000)
        if name == "observe":
            return data(await stagehand.observe(arguments.get("aim")), 6000)
        if name == "extract":
            return data(await stagehand.extract(arguments["what"]), 8000)
        return "No such step: %s" % name

    async def _turn(self, text):
        system = ("You drive a web browser through Stagehand to do the person's task. Use open_page first, then act, "
                  "observe, extract and links (for addresses). Each step returns what happened; do not repeat a step "
                  "that gave its answer. When the task is done, answer the person in "
                  "Markdown with what you found. The VM reaches %s." % ("the internet" if self.kit.web else "no web site"))
        messages = [{"role": "system", "content": system}] + self.history[-12:] + [{"role": "user", "content": text}]
        tools = [{"type": "function", "function": tool} for tool in PLAN]
        for _ in range(STEPS):
            reply = await asyncio.to_thread(complete, self.kit, {"model": self.kit.model, "messages": messages, "tools": tools,
                                                                  "max_tokens": self.kit.reply, "temperature": 0.1})
            messages.append({"role": "assistant", "content": reply.get("content"), **({"tool_calls": reply["tool_calls"]}
                                                                                      if reply.get("tool_calls") else {})})
            calls = reply.get("tool_calls") or []
            if not calls:
                self.history += [{"role": "user", "content": text}, {"role": "assistant", "content": reply.get("content") or ""}]
                return reply.get("content") or ""
            for call in calls:
                name = call["function"]["name"]
                arguments = json.loads(call["function"].get("arguments") or "{}")
                title = " ".join(str(next(iter(arguments.values()), "")).split())[:96]
                with screen.step("Web", "%s %s" % (name.replace("_", " "), title)) as row:
                    try:
                        result = await self.step(name, arguments)
                    except Exception as error:  # the planner hears it and goes on
                        result = "Error: %s: %s" % (type(error).__name__, error)
                        row.fail(result[:200])
                    else:
                        row.output = result[:600]
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
        return "Stopped after %d browser steps. Tell Stagehand what to do next." % STEPS

    def turn(self, text):
        return self.loop.run_until_complete(self._turn(text))


def start(kit):
    return Run(kit)
