"""The harnesses for every kind of work: Yougori's terminal for engines without one (agent_kit.py),
its tools and kits, their launch profiles, and their place in the catalogue, the packages and the
categories. A temporary workspace and home, no network, no harness library installed."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

HARNESSES = Path(__file__).resolve().parents[1] / "site/harnesses"
SITE = HARNESSES.parent
sys.path.insert(0, str(HARNESSES))
import agent_kit
import profiles
import terminal_ui

CATALOG = [{"id": "qwen3-coder", "context_length": 65536}, {"id": "gemma-4"}]
PACKAGES = json.loads((HARNESSES / "packages.json").read_text(encoding="utf-8"))
WEBSITE = {item["id"]: item for item in json.loads((SITE / "harnesses.json").read_text(encoding="utf-8"))}
CATEGORIES = [item["id"] for item in json.loads((SITE / "categories.json").read_text(encoding="utf-8"))]
NEW = {**profiles.KITS, **profiles.OWN_CLIENTS}


class Ran:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **options):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


class Workspace(unittest.TestCase):
    """The terminal's tools work in the current folder (the VM's copy of the person's folder)."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.before = os.getcwd()
        os.chdir(self.folder)
        self.addCleanup(os.chdir, self.before)
        (self.folder / "sales.csv").write_text("name,team,sales\nAna,North,120\nBen,South,95\n", encoding="utf-8")

    def test_files_are_listed_read_written_and_searched_in_the_workspace(self):
        self.assertEqual(agent_kit.list_files(), "sales.csv")
        self.assertIn("Ana,North,120", agent_kit.read_file("sales.csv"))
        self.assertEqual(agent_kit.write_file("out/totals.md", "North 120\n"), "Wrote out/totals.md (10 characters).")
        self.assertEqual((self.folder / "out/totals.md").read_text(encoding="utf-8"), "North 120\n")
        self.assertIn("out/", agent_kit.list_files())
        found = agent_kit.search_files("North")
        self.assertIn("sales.csv:2:Ana,North,120", found.replace("\\", "/"))

    def test_a_missing_file_or_folder_is_a_stop_for_the_model(self):
        with self.assertRaises(agent_kit.Stop):
            agent_kit.read_file("nothing.txt")
        with self.assertRaises(agent_kit.Stop):
            agent_kit.list_files("nowhere")

    @unittest.skipIf(os.name == "nt", "bash runs the commands in the VM")
    def test_a_command_runs_in_the_workspace_with_its_exit_code(self):
        out = agent_kit.run_command("wc -l < sales.csv; exit 3")
        self.assertIn("3", out.splitlines()[0])
        self.assertTrue(out.endswith("[exit code 3]"))

    def test_a_shown_tool_answers_the_model_with_the_error_instead_of_ending_the_turn(self):
        tool = agent_kit.shown(agent_kit.read_file)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(tool("nothing.txt").startswith("Error: No such file"))
            self.assertIn("Ana", tool(path="sales.csv"))
        # The frameworks read the schema from the signature and the docstring.
        import inspect
        self.assertEqual(list(inspect.signature(tool).parameters), ["path"])
        self.assertIn("Args:", tool.__doc__)


class Reach(unittest.TestCase):
    def test_the_web_tools_refuse_without_the_web(self):
        with patch.dict(os.environ, {"SOLVER_REACH": json.dumps({"mode": "inference"})}):
            self.assertFalse(agent_kit.web())
            for call in (lambda: agent_kit.fetch_url("https://example.com"), lambda: agent_kit.web_search("orbit")):
                with self.assertRaises(agent_kit.Stop) as stop:
                    call()
                self.assertIn("--internet", str(stop.exception))

    def test_the_web_is_there_with_full_reach_or_named_sites(self):
        for reach, web in (({"mode": "full"}, True), ({"mode": "sites", "sites": ["example.com"]}, True),
                           ({"mode": "sites", "sites": []}, False), ({"mode": "targets", "targets": ["a:1"]}, False)):
            with patch.dict(os.environ, {"SOLVER_REACH": json.dumps(reach)}):
                self.assertEqual(agent_kit.web(), web, reach)

    def test_a_page_reads_as_text(self):
        page = b"<html><head><style>x{}</style><script>bad()</script></head><body><h1>Hi</h1>&amp; there</body></html>"
        self.assertEqual(agent_kit.page_text(page, "text/html"), "Hi & there")

    def test_the_kit_gives_the_web_tools_only_with_the_web(self):
        args = agent_kit.parse(["langgraph", "--model", "m"])
        for reach, names in (({"mode": "inference"}, {"list_files", "read_file", "write_file", "search_files", "run_command"}),
                             ({"mode": "full"}, {"list_files", "read_file", "write_file", "search_files", "run_command",
                                                 "web_search", "fetch_url"})):
            with patch.dict(os.environ, {"SOLVER_REACH": json.dumps(reach)}):
                kit = agent_kit.Kit("langgraph", args)
                self.assertEqual({tool.__name__ for tool in kit.tools}, names)
                self.assertEqual([t.__name__ for t in kit.pick("read_file", "fetch_url")],
                                 ["read_file"] + (["fetch_url"] if reach["mode"] == "full" else []))


class Answers(unittest.TestCase):
    def test_thinking_markers_are_taken_out_of_an_answer(self):
        self.assertEqual(agent_kit.MARKERS.sub("", "North: 263\n<|channel>thought\n<channel|>").strip(), "North: 263")
        self.assertEqual(agent_kit.MARKERS.sub("", "plain answer"), "plain answer")

    def test_arguments(self):
        args = agent_kit.parse(["vanna", "--model", "m", "--context-window", "65536", "--max-output", "2048",
                                "--answers", '{"database": "data/db.sqlite"}'])
        self.assertEqual((args.kit, args.context_window, args.max_output, args.answers["database"]),
                         ("vanna", 65536, 2048, "data/db.sqlite"))
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            agent_kit.parse(["vanna", "--model", "m", "--answers", "[1]"])


class TypedScreen:
    """The screen as a session uses it: the lines the person types, and what it was told."""

    def __init__(self, typed):
        self.typed = list(typed)
        self.hung_up = threading.Event()
        self.recording = False
        self.recorded = []

    @contextlib.contextmanager
    def working(self, *args, **options):
        yield

    def ask(self, *args):
        return self.typed.pop(0) if self.typed else "/exit"

    def record(self, kind, text, role=None):
        self.recorded.append((kind, role, text))

    def __getattr__(self, name):
        # banner, say, user, warn, error, markdown: shown, nothing more.
        return lambda *args, **options: None


class EarlierChat(unittest.TestCase):
    def test_the_earlier_chat_goes_again_after_a_turn_that_did_not_finish(self):
        given = []

        class Agent:
            def turn(self, text):
                given.append(text)
                if len(given) == 1:
                    raise RuntimeError("the model server went away")
                return "Done."

        module = types.ModuleType("yougori_kit_test")
        module.NAME = "Kit"
        module.start = lambda kit: Agent()
        typed = TypedScreen(["first", "second", "third"])
        kit = types.SimpleNamespace(web=False, workspace="/workspace", model="m",
                                    reach={"mode": "inference", "sites": [], "targets": []})
        args = types.SimpleNamespace(prompt=None, task=None)
        with patch.object(agent_kit, "screen", typed), patch.object(agent_kit, "earlier_chat", lambda: "[Earlier]\nUser: hi"):
            self.assertEqual(agent_kit.session(module, kit, args, io.StringIO()), 0)
        self.assertEqual(given, ["[Earlier]\nUser: hi\n\nfirst", "[Earlier]\nUser: hi\n\nsecond", "third"])
        # The history gets what was typed, never the earlier chat sent with it.
        self.assertEqual([text for kind, role, text in typed.recorded if role == "user"], ["first", "second", "third"])


class Kits(unittest.TestCase):
    def test_every_kit_harness_has_a_kit_that_loads_without_its_library(self):
        for harness in profiles.KITS:
            with self.subTest(harness=harness):
                module = agent_kit.load(harness)
                self.assertTrue(callable(module.start))
                self.assertEqual(module.NAME, profiles.NAMES[harness])
                for name in ("ENGINE", "TAGLINE", "PLACEHOLDER"):
                    self.assertTrue(getattr(module, name, ""), name)

    def test_only_kits_load(self):
        for name in ("../profiles", "nothing", "Lang graph"):
            with self.assertRaises(SystemExit):
                agent_kit.load(name)

    def test_each_kit_harness_has_its_own_colours(self):
        for harness in profiles.KITS:
            with self.subTest(harness=harness):
                self.assertIn(harness, terminal_ui.BRANDS)
                self.assertEqual(terminal_ui.brand(harness), terminal_ui.BRANDS[harness])


class Profiles(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.work = Path(tempfile.mkdtemp())
        clean = patch.dict(os.environ, {}, clear=False)
        clean.start()
        self.addCleanup(clean.stop)
        for name in profiles.PASSED_ON + ("SOLVER_REACH",):
            os.environ.pop(name, None)

    def spec(self, harness, answers=None):
        return profiles.headless(harness, "qwen3-coder", CATALOG, self.home, answers or {}, cwd=self.work, run=Ran())

    def test_a_kit_harness_starts_yougoris_terminal_with_its_kit(self):
        for harness, (name, category) in profiles.KITS.items():
            with self.subTest(harness=harness):
                spec = self.spec(harness)
                json.loads(json.dumps(spec))
                self.assertEqual((spec["protocol"], spec["model_switch"], spec["name"], spec["category"]),
                                 ("terminal", "restart", name, category))
                python = profiles.KIT_PYTHONS.get(harness, profiles.KIT_PYTHON)
                self.assertEqual(spec["argv"][:9], [python, "-I", profiles.AGENT_KIT, harness, "--model", "qwen3-coder",
                                                    "--context-window", "65536", "--max-output", "4096"][:9])
                self.assertEqual(spec["env"]["SOLVER_HARNESS"], harness)
                self.assertEqual(json.loads(spec["env"]["SOLVER_REACH"]), {"mode": "inference", "sites": [], "targets": []})
                self.assertEqual(spec["env"]["OPENAI_BASE_URL"], profiles.BASE)
                for value in spec["env"].values():
                    self.assertFalse(value.startswith("sk-"))

    def test_setup_answers_reach_the_kit_as_one_json_object(self):
        spec = self.spec("vanna", {"database": "data/sales.sqlite"})
        self.assertEqual(spec["argv"][-2:], ["--answers", '{"database":"data/sales.sqlite"}'])
        self.assertNotIn("--answers", self.spec("vanna")["argv"])

    def test_the_new_terminal_clients_start_as_their_own_programs(self):
        expected = {"mini": "mini", "holmes": "holmes", "continue": "cn", "openclaw": "openclaw"}
        for harness, program in expected.items():
            with self.subTest(harness=harness):
                spec = self.spec(harness)
                self.assertEqual((spec["protocol"], spec["argv"][0], spec["category"]),
                                 ("terminal", program, profiles.OWN_CLIENTS[harness][1]))
        self.assertIn("model.model_kwargs.api_base=" + profiles.BASE, self.spec("mini")["argv"])
        self.assertEqual(self.spec("holmes")["env"]["OVERRIDE_MAX_CONTENT_SIZE"], "65536")
        config = (self.home / ".continue/config.yaml").read_text(encoding="utf-8")
        self.assertIn('apiBase: "%s"' % profiles.BASE, config)
        self.assertIn("contextLength: 65536", config)
        self.spec("openclaw")
        claw = json.loads((self.home / ".openclaw/openclaw.json").read_text(encoding="utf-8"))
        self.assertEqual(claw["agents"]["defaults"]["model"]["primary"], "yougori/qwen3-coder")
        self.assertEqual(claw["models"]["providers"]["yougori"]["baseUrl"], profiles.BASE)
        self.assertEqual([m["contextWindow"] for m in claw["models"]["providers"]["yougori"]["models"]], [65536, 32768])


class Catalogue(unittest.TestCase):
    def test_every_new_harness_is_listed_packaged_named_and_categorised(self):
        for harness, (name, category) in NEW.items():
            with self.subTest(harness=harness):
                self.assertIn(harness, PACKAGES)
                self.assertIn(harness, WEBSITE)
                self.assertEqual((WEBSITE[harness]["name"], WEBSITE[harness]["category"]), (name, category))
                self.assertIn(category, CATEGORIES)
                self.assertIn(harness, profiles.HEADLESS)
                self.assertEqual(profiles.NAMES[harness], name)

    def test_every_category_holds_a_harness_and_the_launcher_and_guest_know_it(self):
        import ast
        import vm_host
        used = {item["category"] for item in WEBSITE.values()}
        self.assertEqual(set(CATEGORIES), used)
        self.assertEqual(set(vm_host.CATEGORIES), set(CATEGORIES))
        # The guest runs in the VM (Linux); its list is read from its source here.
        source = (HARNESSES / "vm_guest.py").read_text(encoding="utf-8")
        kinds = next(node.value for node in ast.parse(source).body
                     if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "KINDS")
        self.assertEqual(set(ast.literal_eval(kinds)), set(CATEGORIES))

    @unittest.skipIf(os.name == "nt", "the guest runs in the VM (Linux)")
    def test_the_guest_takes_a_kind_of_work_beside_the_category_every_guest_knows(self):
        import vm_guest
        entry = {"id": "langgraph", "name": "LangGraph", "category": "coding", "kind": "agents", "description": "Graphs.",
                 "questions": [], "base": "base", "memory": 4, "ready": True, "reason": ""}
        fields = {"harnesses": [entry], "vm_base": "base", "vm_memory": 4, "server": "http://127.0.0.1:18700"}
        self.assertEqual(vm_guest.session_fields(fields)["harnesses"][0]["kind"], "agents")
        # A launcher from before the kinds gives none.
        older = dict(entry)
        older.pop("kind")
        self.assertTrue(vm_guest.session_fields({**fields, "harnesses": [older]}))
        for bad in ({"kind": "anything"}, {"category": "agents"}):
            with self.subTest(bad=bad), self.assertRaises(vm_guest.Refusal):
                vm_guest.session_fields({**fields, "harnesses": [{**entry, **bad}]})

    def test_browser_harnesses_start_on_the_chromium_base(self):
        bases = PACKAGES["yougori"]["bases"]["chromium"]
        for harness in ("browseruse", "agenttars", "stagehand"):
            self.assertIn(harness, bases)
            self.assertGreaterEqual(PACKAGES["yougori"]["memory"][harness], 6)


if __name__ == "__main__":
    unittest.main()
