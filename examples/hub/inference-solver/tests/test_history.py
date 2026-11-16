"""The person's chats (the Yougori harness's /history): the VM's guest taking the screen's saves with
the session's token and handing them to the launcher once, asking the launcher for a chat the
screen opens, the launcher keeping them in the inference's private folder, and a /harness switch
into a new VM going on with the chat it was in.

Run: python -I -B examples/hub/inference-solver/tests/test_history.py
"""
import contextlib
import io
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

SITE = Path(__file__).resolve().parents[1] / "site"
sys.path.insert(0, str(SITE))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.append(str(SITE / "harnesses"))
for linux_only in ("fcntl", "pty", "termios"):
    sys.modules.setdefault(linux_only, types.ModuleType(linux_only))
import runtime  # noqa: E402
import vm_guest  # noqa: E402
import vm_host  # noqa: E402


def chat(identity="a" * 32, version=1, title="Fix the parser", turns=2, updated=100):
    return {"id": identity, "title": title, "started": 1, "updated": updated, "version": version, "harness": "opencode",
            "name": "OpenCode", "harnesses": ["OpenCode"], "model": "m",
            "turns": [{"role": "user" if i % 2 == 0 else "assistant", "text": "turn %d" % i, "harness": "OpenCode"}
                      for i in range(turns)]}


class Guest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        (root / "home").mkdir()
        (root / "harness-name").write_text("yougori\n")
        self.root = root
        self.state = vm_guest.State(root, root / "home")
        self.state.started = True
        server = ThreadingHTTPServer(("127.0.0.1", 0), vm_guest.Control)
        server.state = self.state
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.vm_url = "http://127.0.0.1:%d" % server.server_port
        self.control = vm_host.Control("http://abc123.localhost:%d/" % server.server_port)

    def ask(self, path, data, token=None):
        """A request from the screen inside the VM, with the session's token."""
        with patch.object(runtime, "GUEST", self.vm_url):
            return runtime.guest(path, data, self.state.token if token is None else token)

    def test_only_the_screen_saves_lists_and_opens(self):
        for path in ("/history/save", "/history/list", "/history/open"):
            with self.assertRaisesRegex(ValueError, "The history is the screen's own"):
                self.ask(path, {"chat": chat()}, "wrong")
        with self.assertRaisesRegex(ValueError, "Invalid chat"):
            self.ask("/history/save", {"chat": {**chat(), "id": "../etc"}})
        with self.assertRaisesRegex(ValueError, "Invalid chat"):
            self.ask("/history/save", {"chat": {**chat(), "turns": [{"role": "system", "text": "x"}]}})

    def test_without_a_launcher_that_keeps_them_chats_stay_in_the_vm(self):
        self.assertEqual(self.ask("/history/save", {"chat": chat()}), {"saved": True, "kept": False})
        listed = self.ask("/history/list", {})
        self.assertEqual((listed["kept"], [item["id"] for item in listed["chats"]]), (False, ["a" * 32]))
        self.assertEqual(self.ask("/history/open", {"id": "a" * 32})["chat"]["title"], "Fix the parser")
        with self.assertRaisesRegex(ValueError, "not kept on this computer"):
            self.ask("/history/open", {"id": "b" * 32})
        self.assertNotIn("history", self.control.post("/poll", {}))

    def test_saves_reach_the_launcher_once_and_the_newest_wins(self):
        self.control.post("/history/index", {"index": [vm_host.chat_summary(chat("c" * 32, title="Older chat", updated=50))]})
        self.ask("/history/save", {"chat": chat(version=2, turns=4)})
        self.ask("/history/save", {"chat": chat(version=1, turns=2)})
        packet = self.control.post("/poll", {})
        self.assertEqual([(item["id"], item["version"]) for item in packet["history"]], [("a" * 32, 2)])
        self.assertNotIn("history", self.control.post("/poll", {}))
        listed = self.ask("/history/list", {})
        self.assertTrue(listed["kept"])
        self.assertEqual([item["title"] for item in listed["chats"]], ["Fix the parser", "Older chat"])
        self.assertEqual(listed["chats"][0]["turns"], 4)

    def test_a_chat_kept_on_the_computer_is_asked_of_the_launcher(self):
        self.control.post("/history/index", {"index": [vm_host.chat_summary(chat("c" * 32))]})
        opened = {}
        thread = threading.Thread(target=lambda: opened.update(self.ask("/history/open", {"id": "c" * 32})))
        thread.start()
        for _ in range(50):
            packet = self.control.post("/poll", {})
            if packet.get("history_open"):
                break
        self.assertEqual(packet["history_open"], ["c" * 32])
        with self.assertRaises(ValueError):
            self.control.post("/history/body", {"id": "d" * 32, "chat": chat("d" * 32)})
        self.control.post("/history/body", {"id": "c" * 32, "chat": chat("c" * 32, title="Kept chat")})
        thread.join(10)
        self.assertEqual(opened["chat"]["title"], "Kept chat")
        # One the launcher does not have.
        missing = {}
        thread = threading.Thread(target=lambda: missing.update(error=self.open_error("e" * 32)))
        thread.start()
        for _ in range(50):
            if self.control.post("/poll", {}).get("history_open"):
                break
        self.control.post("/history/body", {"id": "e" * 32, "missing": True})
        thread.join(10)
        self.assertEqual(missing["error"], "That chat is not kept on this computer.")

    def open_error(self, identity):
        try:
            self.ask("/history/open", {"id": identity})
        except ValueError as error:
            return str(error)
        return None

    def test_the_end_is_said_once_the_chats_have_gone_and_finish_brings_the_last(self):
        self.control.post("/history/index", {"index": []})
        self.ask("/history/save", {"chat": chat()})
        with self.state.lock:
            self.state.exit_code = 0
        # The last chats come with the end (the launcher keeps them before it reads the end); chats
        # that do not fit one packet hold the end back.
        packet = self.control.post("/poll", {})
        self.assertEqual((len(packet["history"]), packet["exit"]), (1, 0))
        with patch.object(vm_guest, "PACKET_HISTORY", 10):
            self.ask("/history/save", {"chat": chat("b" * 32)})
            self.ask("/history/save", {"chat": chat("c" * 32)})
            packet = self.control.post("/poll", {})
            self.assertEqual((len(packet["history"]), packet["exit"]), (1, None))
            packet = self.control.post("/poll", {})
            self.assertEqual((len(packet["history"]), packet["exit"]), (1, 0))
        self.ask("/history/save", {"chat": chat(version=5)})
        finished = self.control.post("/finish", {})
        self.assertEqual([item["version"] for item in finished["history"]], [5])

    def test_a_new_vm_switch_names_the_chat_it_goes_on_with(self):
        self.state.switchable = {"kilo"}
        request = {"harness": "kilo", "model": "m", "answers": {}, "mode": "new-vm", "chat": "a" * 32}
        self.ask("/switch", request)
        for _ in range(20):
            packet = self.control.post("/poll", {})
            if "switch" in packet:
                break
        self.assertEqual(packet["switch"]["chat"], "a" * 32)
        # A chat id that is not one is dropped.
        state = vm_guest.State(self.root, self.root / "home")
        state.started, state.switchable = True, {"kilo"}
        state.switch_begin({**request, "chat": "../x"})
        self.assertNotIn("chat", state.switch_take())

    def test_the_session_says_which_chat_the_screen_opens(self):
        fields = {"harnesses": [{"id": "yougori", "name": "Yougori", "category": "coding", "description": "", "questions": [],
                                 "base": "base", "memory": 4, "ready": True}],
                  "vm_base": "base", "vm_memory": 4, "server": "https://argon.example"}
        data = {"harness": "yougori", "answers": {}, "secrets": {}, "reach": {"mode": "inference"}, **fields,
                "history": {"resume": "a" * 32}}
        vm_guest.harness_session(data, "yougori", {}, self.root / "home", "token")
        self.assertEqual(runtime.session(self.root / "home")["resume"], "a" * 32)
        vm_guest.harness_session({**data, "history": {"resume": "nope"}}, "yougori", {}, self.root / "home", "token")
        self.assertNotIn("resume", runtime.session(self.root / "home"))


class Launcher(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_chats_are_kept_newest_first_and_the_newest_version_wins(self):
        self.assertTrue(vm_host.keep_chat(self.root, chat("a" * 32, updated=100)))
        self.assertTrue(vm_host.keep_chat(self.root, chat("b" * 32, title="Second", updated=200)))
        self.assertEqual([item["title"] for item in vm_host.history_index(self.root)], ["Second", "Fix the parser"])
        self.assertTrue(vm_host.keep_chat(self.root, chat("a" * 32, version=3, turns=6, updated=300)))
        self.assertFalse(vm_host.keep_chat(self.root, chat("a" * 32, version=2, updated=400)), "an older save loses")
        index = vm_host.history_index(self.root)
        self.assertEqual([(item["id"][0], item["turns"]) for item in index], [("a", 6), ("b", 2)])
        self.assertFalse(vm_host.keep_chat(self.root, {**chat(), "id": "../../x"}))
        files = sorted(path.name for path in (self.root / "history").iterdir())
        self.assertEqual(files, ["a" * 32 + ".json", "b" * 32 + ".json", "index.json"])
        # A damaged list is rebuilt from the chats.
        (self.root / "history" / "index.json").write_text("{", encoding="utf-8")
        self.assertEqual([item["id"][0] for item in vm_host.history_index(self.root)], ["a", "b"])

    def test_an_open_is_answered_with_the_chat_or_missing(self):
        vm_host.keep_chat(self.root, chat())
        sent = []

        class Control:
            def post(self, path, data):
                sent.append((path, data))
                return {}
        done = threading.Event()
        vm_host.answer_open(Control(), self.root, "a" * 32, done)
        vm_host.answer_open(Control(), self.root, "f" * 32, done)
        self.assertEqual(sent[0][1]["chat"]["title"], "Fix the parser")
        self.assertEqual(sent[1][1], {"id": "f" * 32, "missing": True})

    def test_a_switch_into_a_new_vm_goes_on_with_the_chat(self):
        calls = []
        results = [vm_host.Relaunch({"harness": "kilo", "name": "Kilo", "category": "coding", "memory": 4, "answers": {},
                                     "model": "m", "chat": "a" * 32}), 0]

        def launch(name, server, key_file, model, workspace, arguments, interactive=True, **options):
            calls.append(options)
            options["reviews"].append("review")
            return results[len(calls) - 1]
        with patch.object(vm_host, "launch", launch), contextlib.redirect_stdout(io.StringIO()):
            vm_host.run_session("opencode", "https://argon.example", Path("key"), "m", None, False, known={"kilo"})
        self.assertEqual([call["resume"] for call in calls], [None, "a" * 32])


if __name__ == "__main__":
    unittest.main()
