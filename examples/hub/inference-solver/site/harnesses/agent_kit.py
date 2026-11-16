"""Yougori's terminal for the harnesses that bring an engine but no terminal of their own: the agent
frameworks (LangGraph, Deep Agents, CrewAI, smolagents, Strands, Google ADK, Microsoft Agent
Framework) and the data, research, office, service, finance and science agents built as libraries.

Each harness's own engine runs every turn: its kit (kits/NAME.py) builds that engine's agent on the
model this session chose, through the VM's relay, and gives it this terminal's tools as its own kind
of tool. This terminal draws the session in the Yougori screen's look (terminal_ui.py), as the
security harnesses' terminals do: the banner in the harness's colours, the person's turns after `›`,
each tool call on a row that turns while it runs, and the answer in Markdown.

The tools work in the workspace (the VM's copy of the person's folder, which comes back only after
their review): list, read, write and search files, run a command, and fetch a web page when the VM
reaches the web. Whatever a library prints goes to ~/.yougori/NAME.log, so it never draws over the
screen.

  python3 agent_kit.py NAME --model ID [--context-window N] [--max-output N] [--answers JSON]
                            [--task TEXT | --print TEXT]
"""
import argparse
import contextlib
import functools
import html
import importlib.util
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
import warnings

HERE = Path(__file__).resolve().parent
# Last, so a library that imports a module named like one of Yougori's files beside this one
# (transport, runtime, relay...) still gets its own.
if str(HERE) not in sys.path:
    sys.path.append(str(HERE))
from terminal_ui import earlier_chat, screen  # noqa: E402

BASE = "http://127.0.0.1:8080/v1"
LOCAL_KEY = "solver-local-relay"
TIKTOKEN = "/opt/solver/tiktoken"
# What a tool gives the model at most, in characters: a file read, a command's output, a page.
READ_MOST, OUTPUT_MOST, PAGE_MOST = 60000, 20000, 40000
COMMAND_SECONDS = 300
# The keys under the input box.
KEYS = (("enter", "send"), ("↑↓", "earlier lines"), ("/exit", "leave"))
# A model's thinking markers left in its answer (Gemma's <|channel>thought ... <channel|>, empty or
# not closed): not part of what the person reads.
MARKERS = re.compile(r"<\|channel\|?>(?:thought)?|<channel\|>")
LEAVE = ("/exit", "/quit")


def reach():
    """What the VM reaches besides the inference (SOLVER_REACH, the launcher's word): its mode, and
    the sites or targets named."""
    try:
        value = json.loads(os.environ.get("SOLVER_REACH") or "{}")
    except ValueError:
        value = {}
    if not isinstance(value, dict):
        value = {}
    mode = value.get("mode") if value.get("mode") in ("inference", "sites", "targets", "full") else "inference"
    return {"mode": mode, "sites": [s for s in value.get("sites") or [] if isinstance(s, str)],
            "targets": [t for t in value.get("targets") or [] if isinstance(t, str)]}


def reach_words(value=None):
    value = value or reach()
    if value["mode"] == "full":
        return "the internet and this inference"
    if value["mode"] == "sites":
        return ", ".join(value["sites"]) + " and this inference" if value["sites"] else "this inference only"
    if value["mode"] == "targets":
        return ", ".join(value["targets"]) + " and this inference" if value["targets"] else "this inference only"
    return "this inference only"


def web():
    """Whether the VM reaches any web site."""
    value = reach()
    return value["mode"] == "full" or (value["mode"] == "sites" and bool(value["sites"]))


class Stop(Exception):
    """A tool that cannot do what it was asked: its words go back to the model as the result."""


def _inside(path):
    """A path the model gave, in the workspace when it is relative."""
    path = Path(os.path.expanduser(str(path or ".")))
    return path if path.is_absolute() else Path.cwd() / path


def _cut(text, most):
    text = text if isinstance(text, str) else str(text)
    if len(text) <= most:
        return text
    return text[:most] + "\n[cut: %d more characters]" % (len(text) - most)


def _short(value, most=72):
    value = " ".join(str(value).split())
    return value if len(value) <= most else value[:most - 1] + "…"


# The tools. Each is a plain function with type hints and a docstring, which every framework reads
# as the tool's schema; shown() draws its call on a row of its own.

def list_files(path: str = ".") -> str:
    """List the files and folders in a folder of the workspace, one level deep. Folders end with /.

    Args:
        path: The folder, relative to the workspace. Defaults to the workspace itself.
    """
    folder = _inside(path)
    if not folder.is_dir():
        raise Stop("No such folder: %s" % path)
    names = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    shown = [p.name + ("/" if p.is_dir() else "") for p in names if p.name not in (".git", "node_modules", "__pycache__")]
    return "\n".join(shown[:500]) + ("\n[%d more]" % (len(shown) - 500) if len(shown) > 500 else "") or "(empty)"


def read_file(path: str) -> str:
    """Read a text file of the workspace and return its contents.

    Args:
        path: The file, relative to the workspace.
    """
    file = _inside(path)
    if not file.is_file():
        raise Stop("No such file: %s" % path)
    data = file.read_bytes()
    if b"\0" in data[:4096]:
        raise Stop("%s is not a text file (%d bytes)." % (path, len(data)))
    return _cut(data.decode("utf-8", "replace"), READ_MOST)


def write_file(path: str, content: str) -> str:
    """Write a text file in the workspace, replacing it if it exists; its folders are made as needed.

    Args:
        path: The file, relative to the workspace.
        content: The whole text the file should hold.
    """
    file = _inside(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(content if isinstance(content, str) else str(content), encoding="utf-8")
    return "Wrote %s (%d characters)." % (path, len(content))


def run_command(command: str) -> str:
    """Run a shell command (bash) in the workspace and return its output and exit code. Use it to
    run programs, scripts and tests, or to install nothing: the VM has no package downloads.

    Args:
        command: The command line, as typed in bash.
    """
    try:
        done = subprocess.run(["bash", "-lc", command], cwd=Path.cwd(), capture_output=True, text=True,
                              timeout=COMMAND_SECONDS, stdin=subprocess.DEVNULL, errors="replace")
    except subprocess.TimeoutExpired as late:
        out = (late.stdout or "") + (late.stderr or "")
        return _cut(out if isinstance(out, str) else out.decode("utf-8", "replace"), OUTPUT_MOST) + \
            "\n[stopped after %d seconds]" % COMMAND_SECONDS
    out = (done.stdout or "") + (("\n" if done.stdout and done.stderr else "") + done.stderr if done.stderr else "")
    return _cut(out.strip() or "(no output)", OUTPUT_MOST) + "\n[exit code %d]" % done.returncode


def search_files(pattern: str, path: str = ".") -> str:
    """Search the workspace's files for a regular expression and return the matching lines with
    their file and line number.

    Args:
        pattern: The regular expression.
        path: The folder or file to search, relative to the workspace.
    """
    try:
        done = subprocess.run(["rg", "--line-number", "--no-heading", "--color", "never", "--max-count", "50", "-e", pattern,
                               str(_inside(path))], capture_output=True, text=True, timeout=60, errors="replace")
    except FileNotFoundError:
        done = subprocess.run(["grep", "-rn", "-E", "-m", "50", pattern, str(_inside(path))], capture_output=True, text=True,
                              timeout=60, errors="replace")
    found = done.stdout.replace(str(Path.cwd()) + "/", "")
    return _cut(found.strip(), OUTPUT_MOST) or "No matches."


TAGS = re.compile(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>|<[^>]+>")


def page_text(raw, kind):
    """A fetched page as text: HTML without its tags, scripts and styles."""
    text = raw.decode("utf-8", "replace")
    if "html" in kind or text.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        text = html.unescape(TAGS.sub(" ", text))
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def fetch_url(url: str) -> str:
    """Fetch a web page or file over HTTP(S) and return its text (a web page without its HTML tags).

    Args:
        url: The full address, such as https://example.com/page.
    """
    if not re.match(r"(?i)^https?://", url or ""):
        raise Stop("Give a full http:// or https:// address.")
    if not web():
        raise Stop("This VM reaches only the inference, not the web. Start Yougori with --internet sites "
                   "(naming the sites) or --internet full to let it fetch pages.")
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Yougori agent)", "Accept": "*/*"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read(4 * 1024 * 1024)
            kind = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as error:
        raise Stop("%s answered HTTP %d." % (url, error.code))
    except (urllib.error.URLError, OSError) as error:
        raise Stop("Could not fetch %s: %s" % (url, getattr(error, "reason", error)))
    return _cut(page_text(raw, kind), PAGE_MOST)


RESULT = re.compile(r'(?is)<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?(?:class="result__snippet"[^>]*>(.*?)</a>)?')


def web_search(query: str) -> str:
    """Search the web (DuckDuckGo) and return the first results: title, address and a snippet each.
    Fetch an address with fetch_url to read the page.

    Args:
        query: What to search for, in words.
    """
    if not web():
        raise Stop("This VM reaches only the inference, not the web. Start Yougori with --internet full, or "
                   "--internet sites naming html.duckduckgo.com and the sites to read, to search the web.")
    from urllib.parse import parse_qs, quote_plus, unquote, urlparse
    request = urllib.request.Request("https://html.duckduckgo.com/html/?q=" + quote_plus(query),
                                     headers={"User-Agent": "Mozilla/5.0 (Yougori agent)"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            page = response.read(2 * 1024 * 1024).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as error:
        raise Stop("The search did not answer: %s" % getattr(error, "reason", error))
    found = []
    for href, title, snippet in RESULT.findall(page):
        link = parse_qs(urlparse(html.unescape(href)).query).get("uddg", [href])[0]
        found.append("%s\n%s\n%s" % (page_text(title.encode(), "html"), unquote(link), page_text((snippet or "").encode(), "html")))
        if len(found) == 8:
            break
    return "\n\n".join(found) or "No results."


# How each tool's call is titled on its row.
TITLES = {
    "web_search": lambda a: "Search the web for " + _short(a.get("query", "")),
    "list_files": lambda a: "List " + _short(a.get("path") or "."),
    "read_file": lambda a: "Read " + _short(a.get("path", "")),
    "write_file": lambda a: "Write " + _short(a.get("path", "")),
    "run_command": lambda a: "$ " + _short(a.get("command", ""), 96),
    "search_files": lambda a: "Search " + _short(a.get("pattern", "")) + (" in " + _short(a["path"]) if a.get("path") not in (None, ".") else ""),
    "fetch_url": lambda a: "Fetch " + _short(a.get("url", ""), 96),
}
KINDS = {"run_command": "Shell", "fetch_url": "Web", "web_search": "Web"}


def shown(function, title=None, kind=None):
    """`function` with its call drawn on a row of its own: its kind, its title, then done or failed
    with how long it took and its first lines of output. A Stop goes back to the model as the
    tool's result; any other error too, so one failed call never ends the turn."""
    name = function.__name__
    title = title or TITLES.get(name) or (lambda a: name.replace("_", " ").capitalize())
    kind = kind or KINDS.get(name, "Tool")
    import inspect
    signature = inspect.signature(function)

    @functools.wraps(function)
    def call(*args, **kwargs):
        try:
            bound = signature.bind_partial(*args, **kwargs)
            given = dict(bound.arguments)
        except TypeError:
            given = dict(kwargs)
        with screen.step(kind, title(given)) as step:
            try:
                result = function(*args, **kwargs)
            except Stop as stop:
                step.fail(str(stop))
                return "Error: " + str(stop)
            except Exception as error:  # the model hears what went wrong and goes on
                step.fail(_short("%s: %s" % (type(error).__name__, error), 200))
                return "Error: %s: %s" % (type(error).__name__, error)
            text = result if isinstance(result, str) else json.dumps(result, default=str)
            if name in ("run_command", "search_files", "fetch_url", "web_search"):
                step.output = text
            elif name == "read_file":
                step.note = "%d lines" % (text.count("\n") + 1)
            return result
    return call


class Kit:
    """What a kit is given: the model through the relay, its window and reply budget, the setup
    answers, the reach, and the tools, each drawn as it runs."""

    def __init__(self, name, args):
        self.name = name
        self.model = args.model
        self.window = args.context_window
        self.reply = args.max_output
        self.base_url = os.environ.get("OPENAI_BASE_URL") or BASE
        self.api_key = LOCAL_KEY
        self.answers = args.answers
        self.reach = reach()
        self.web = web()
        self.workspace = Path.cwd()
        self.log = Path.home() / ".yougori" / (name + ".log")
        tools = [list_files, read_file, write_file, search_files, run_command]
        if self.web:
            tools += [web_search, fetch_url]
        self.plain_tools = tools
        self.tools = [shown(tool) for tool in tools]

    def tool(self, function, title=None, kind=None):
        """A kit's own tool, drawn as this terminal's are."""
        return shown(function, title, kind)

    def pick(self, *names):
        """This terminal's tools of these names that this session has (the web ones only with the web)."""
        return [tool for tool, plain in zip(self.tools, self.plain_tools) if plain.__name__ in names]

    # What a kit shows besides the tool rows.
    def say(self, text):
        screen.note(text)

    def thought(self, text):
        if text and text.strip():
            screen.thought(text.strip())

    def markdown(self, text):
        if text and text.strip():
            screen.markdown(text.strip())

    def step(self, kind, title):
        return screen.step(kind, title)

    def instructions(self, role):
        """The system words every kit gives its agent: who it is, where it works and what it may reach."""
        return ("%s You work in a Linux VM; the current folder (%s) is the person's project, a copy they review before "
                "any change reaches their computer. Use the tools to look at files, change them and run commands, "
                "rather than guessing. The VM reaches %s. Answer in English unless the person writes in another language. Use Markdown, and say "
                "plainly what you did and what you found." % (role, self.workspace, reach_words(self.reach)))


def blocking():
    """The terminal's input and output back to blocking: a program a library starts on the same
    terminal (Chromium does) can leave them non-blocking, and the screen's lines would then be lost."""
    for fd in (0, 1):
        with contextlib.suppress(OSError, ValueError):
            os.set_blocking(fd, True)


class Steady:
    """The terminal as the screen writes to it: a write that finds it non-blocking and full puts it
    back to blocking and goes on, instead of losing the line."""

    def __init__(self, stream):
        self.stream = stream

    def _again(self, act):
        for _ in range(200):
            try:
                return act()
            except BlockingIOError:
                blocking()
                time.sleep(0.01)
        return None

    def write(self, text):
        return self._again(lambda: self.stream.write(text))

    def flush(self):
        return self._again(self.stream.flush)

    def __getattr__(self, name):
        return getattr(self.stream, name)


@contextlib.contextmanager
def quiet(log):
    """While a turn runs: what the library prints (its own progress, rich panels, warnings) goes to
    the log, never over the screen, which keeps writing to the terminal itself."""
    try:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            yield
    finally:
        blocking()


def load(name):
    """The kit module kits/NAME.py."""
    path = HERE / "kits" / (name + ".py")
    if not re.fullmatch(r"[a-z0-9_]+", name) or not path.is_file():
        raise SystemExit("No such Yougori kit: %s" % name)
    spec = importlib.util.spec_from_file_location("yougori_kit_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def setup_logging(path):
    """Every library's logging, warnings and stray stderr lines go to `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = open(path, "a", encoding="utf-8", buffering=1)
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    logging.captureWarnings(True)
    warnings.simplefilter("default")
    # The file descriptor too: a library's subprocess or C code writing to stderr.
    with contextlib.suppress(OSError, ValueError):
        os.dup2(stream.fileno(), 2)
    sys.stderr = stream
    return stream


def session(module, kit, args, log):
    """The session: the banner, then turn after turn until /exit, Ctrl+D or Ctrl+C at the box."""
    with screen.working("Starting " + module.NAME, hint=""), quiet(log):
        agent = module.start(kit)
    if not args.prompt:
        facts = [("Engine", getattr(module, "ENGINE", module.NAME)),
                 ("Tools", getattr(agent, "tools_words", None) or ("files, shell and the web" if kit.web else "files and shell in this VM")),
                 ("Folder", str(kit.workspace)), ("Reach", reach_words(kit.reach))]
        facts += list(getattr(agent, "facts", ()) or ())
        screen.banner(module.NAME, kit.model, facts, getattr(module, "TAGLINE", ""))
        hello = getattr(agent, "hello", None)
        if hello:
            screen.say(hello)
    first = (args.task or "").strip()
    placeholder = getattr(module, "PLACEHOLDER", "Tell %s what to do" % module.NAME)
    # An earlier chat this harness takes over (the Yougori screen wrote it): it goes with the first
    # prompt, as it does for the screen's other harnesses.
    earlier = earlier_chat()
    if earlier and not args.prompt:
        screen.say("%s takes over the earlier chat with your first message." % module.NAME)
    screen.recording = True
    while True:
        if args.prompt:
            text = args.prompt
        elif first:
            text, first = first, ""
            screen.user(text)
        else:
            text = screen.ask(placeholder, KEYS, LEAVE, module.NAME.lower().replace(" ", "-") + " > ", module.__name__)
        if text.strip() in LEAVE:
            return 0
        if not text.strip():
            continue
        started = time.monotonic()
        screen.record("turn", text, "user")
        # The earlier chat goes with each prompt until a turn with it finished: one stopped or
        # failed may not have kept it.
        sent = earlier + "\n\n" + text if earlier else text
        try:
            with screen.working(getattr(module, "WORKING", "Working")), quiet(log):
                answer = agent.turn(sent)
        except KeyboardInterrupt:
            if screen.hung_up.is_set():
                raise  # the pane ends the harness (/new, another model): no "Stopped", just leave
            stopped = getattr(agent, "stopped", None)
            if stopped:
                with contextlib.suppress(Exception), quiet(log):
                    stopped()
            screen.warn("Stopped. Tell %s what to do next." % module.NAME)
            if args.prompt:
                return 130
            continue
        except Exception as error:  # the session goes on after a failed turn
            log.write(traceback.format_exc())
            screen.error("%s could not finish this turn: %s" % (module.NAME, _short("%s: %s" % (type(error).__name__, error), 400)))
            if args.prompt:
                return 1
            continue
        earlier = ""
        answer = MARKERS.sub("", str(answer or "")).strip()
        if answer:
            screen.markdown(answer)
        else:
            screen.say("%s finished without a reply (%s)." % (module.NAME, _seconds(time.monotonic() - started)))
        screen.record("turn", answer or "(no reply)", "assistant")
        if args.prompt:
            return 0


def _seconds(seconds):
    return "%d s" % seconds if seconds < 60 else "%d min %02d s" % divmod(int(seconds), 60)


def parse(argv=None):
    parser = argparse.ArgumentParser(description="A Yougori terminal for an agent framework's engine.")
    parser.add_argument("kit")
    parser.add_argument("--model", required=True)
    parser.add_argument("--context-window", type=int, default=32768)
    parser.add_argument("--max-output", type=int, default=4096)
    parser.add_argument("--answers", type=json.loads, default={})
    first = parser.add_mutually_exclusive_group()
    first.add_argument("--task", help="The first instruction; the session then goes on in this terminal")
    first.add_argument("--print", dest="prompt", help="Run one instruction, print the answer and leave")
    args = parser.parse_args(argv)
    if not isinstance(args.answers, dict):
        parser.error("--answers takes a JSON object.")
    return args


def main(argv=None):
    args = parse(argv)
    module = load(args.kit)
    # Every library is told the relay alone, by every name the libraries read.
    for name, value in (("OPENAI_API_KEY", LOCAL_KEY), ("OPENAI_BASE_URL", BASE), ("OPENAI_API_BASE", BASE)):
        os.environ.setdefault(name, value)
    # The tokenizer tables tiktoken would fetch from the internet, in the image (Containerfile.yougori).
    if Path(TIKTOKEN).is_dir():
        os.environ.setdefault("TIKTOKEN_CACHE_DIR", TIKTOKEN)
    for name in getattr(module, "QUIET_ENV", {}).items() if isinstance(getattr(module, "QUIET_ENV", None), dict) else ():
        os.environ.setdefault(*name)
    kit = Kit(args.kit, args)
    log = setup_logging(kit.log)
    terminal = Steady(sys.stdout)
    # The screen keeps writing to the terminal while a turn's prints go to the log.
    screen.stream = lambda: terminal
    try:
        with screen.session():
            return session(module, kit, args, log)
    except (KeyboardInterrupt, EOFError):
        return 0
    except Exception as error:
        log.write(traceback.format_exc())
        screen.error("%s could not start: %s" % (module.NAME, _short("%s: %s" % (type(error).__name__, error), 600)))
        screen.say("Its log is %s in the VM." % kit.log)
        return 1


if __name__ == "__main__":
    sys.exit(main())
