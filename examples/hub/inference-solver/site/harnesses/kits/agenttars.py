"""Agent TARS: ByteDance's multimodal agent (the UI-TARS family) for browser and computer tasks. Its own
agent server runs headless in the VM (agent-tars serve, on loopback), with its browser, search, file
and command tools, on the relay's model; each turn is a query to that server, answered when the
agent is done. Its working files are in the person's folder."""
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from terminal_ui import screen

NAME = "Agent TARS"
ENGINE = "Agent TARS server · browser, search, files and commands"
TAGLINE = "a multimodal agent for browser and computer tasks"
PLACEHOLDER = "Give Agent TARS a task"
WORKING = "Agent TARS is working"
PORT = 18901


def call(method, path, body=None, token=None, timeout=30):
    request = urllib.request.Request("http://127.0.0.1:%d%s" % (PORT, path), method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", **({"x-csrf-token": token} if token else {})})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read() or b"{}")


def words(result):
    """The agent's answer in a query's result: its text, wherever the result keeps it."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        for key in ("content", "text", "finalAnswer", "answer", "result"):
            if result.get(key):
                return words(result[key])
        return json.dumps(result, ensure_ascii=False)[:4000]
    if isinstance(result, list):
        return "\n".join(filter(None, (words(item) for item in result)))
    return str(result or "")


class Run:
    def __init__(self, kit):
        self.kit = kit
        program = shutil.which("agent-tars") or "/opt/solver/tool/node_modules/.bin/agent-tars"
        self.log = (Path.home() / ".yougori" / "agent-tars-server.log").open("w")
        self.server = subprocess.Popen(
            [program, "serve", "--port", str(PORT), "--host", "127.0.0.1", "--quiet", "--workspace", str(kit.workspace),
             "--model.provider", "openai", "--model.id", kit.model, "--model.apiKey", kit.api_key, "--model.baseURL", kit.base_url],
            stdout=self.log, stderr=self.log, stdin=subprocess.DEVNULL, start_new_session=True)
        # Its server is in a process group of its own: it stops with this terminal however that ends
        # (/exit, /new, another model), so the next start finds its port free.
        import atexit
        atexit.register(self.stop)
        for _ in range(240):
            if self.server.poll() is not None:
                raise ValueError("Agent TARS's server stopped as it started; see %s in the VM." % self.log.name)
            try:
                call("GET", "/api/v1/csrf-token", timeout=2)
                break
            except (urllib.error.URLError, OSError, ValueError):
                time.sleep(0.5)
        else:
            raise ValueError("Agent TARS's server did not become ready.")
        self.past = []
        self.tools_words = 'its own browser, search, file and command tools'
        self.facts = [("Server", "agent-tars serve on 127.0.0.1:%d, in this VM" % PORT)]

    def turn(self, text):
        query = text if not self.past else "Earlier in this conversation:\n%s\n\nNow: %s" % (
            "\n".join("- %s → %s" % pair for pair in self.past[-4:]), text)
        token = call("GET", "/api/v1/csrf-token")["token"]
        with screen.step("Agent", "Agent TARS works on the task") as step:
            answer = call("POST", "/api/v1/oneshot/query", {"query": query, "sessionName": text[:80], "sessionTags": ["yougori"]},
                          token=token, timeout=1800)
            step.note = "session %s" % str(answer.get("sessionId", ""))[:8]
        reply = words(answer.get("result"))
        self.past.append((text[:200], reply[:300]))
        return reply

    def stop(self):
        server = getattr(self, "server", None)
        if server and server.poll() is None:
            try:
                os.killpg(server.pid, 15)
                server.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(server.pid, 9)
                except OSError:
                    pass


def start(kit):
    return Run(kit)
