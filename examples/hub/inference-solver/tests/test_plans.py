"""A person's own plans (a Claude or ChatGPT subscription in Claude Code or Codex): the screen asking
for one with the session's token, the launcher deciding its sites and sending the sign-in chosen
(this computer's own, or the one Yougori kept), the VM's proxy opening to those sites alone, and a
sign-in the client makes or renews in the VM going back to the launcher, which keeps it and gives it
back to this computer while the computer's own is as it was.

Run: python -I -B examples/hub/inference-solver/tests/test_plans.py
"""
import json
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

SITE = Path(__file__).resolve().parents[1] / "site"
sys.path.insert(0, str(SITE))
sys.path.append(str(SITE / "harnesses"))
for linux_only in ("fcntl", "pty", "termios"):
    sys.modules.setdefault(linux_only, types.ModuleType(linux_only))
import runtime  # noqa: E402
import vm_guest  # noqa: E402
import vm_host  # noqa: E402

SIGNED = json.dumps({"claudeAiOauth": {"accessToken": "a", "refreshToken": "r", "expiresAt": 1}})
CODEX = json.dumps({"auth_mode": "chatgpt", "tokens": {"access_token": "a", "refresh_token": "r"}})


class Guest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        (root / "home").mkdir()
        (root / "harness-name").write_text("yougori\n")
        self.home = root / "home"
        self.state = vm_guest.State(root, self.home)
        self.state.started = True
        server = ThreadingHTTPServer(("127.0.0.1", 0), vm_guest.Control)
        server.state = self.state
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.vm_url = "http://127.0.0.1:%d" % server.server_port
        self.control = vm_host.Control("http://abc123.localhost:%d/" % server.server_port)
        # The proxy itself is vm_tunnel's (test_vm_tunnels): here, only that it starts.
        self.served = []
        patcher = patch.object(vm_guest, "serve_proxy", self.served.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def ask(self, data, token=None):
        with patch.object(runtime, "GUEST", self.vm_url):
            return runtime.guest("/subscription", data, self.state.token if token is None else token, timeout=90)

    def asked(self, data):
        """The screen's request on a thread of its own: it waits for the launcher's answer."""
        result = {}

        def run():
            try:
                result["value"] = self.ask(data)
            except ValueError as error:
                result["error"] = str(error)
        thread = threading.Thread(target=run)
        thread.start()
        for _ in range(50):
            packet = self.control.post("/poll", {})
            if "subscription" in packet:
                return thread, result, packet["subscription"]
        self.fail("no plan request reached the launcher")

    def test_only_the_screen_asks_and_the_sites_open_with_the_sign_in(self):
        with self.assertRaisesRegex(ValueError, "the screen's own"):
            self.ask({"plan": "chatgpt", "use": "computer"}, "wrong")
        with self.assertRaisesRegex(ValueError, "Invalid plan"):
            self.ask({"plan": "someone", "use": "computer"})
        thread, result, taken = self.asked({"plan": "chatgpt", "use": "computer"})
        self.assertEqual({k: taken[k] for k in ("plan", "use")}, {"plan": "chatgpt", "use": "computer"})
        self.control.post("/subscription/granted", {"id": taken["id"], "sites": ["chatgpt.com", "openai.com"], "credential": CODEX})
        thread.join(10)
        self.assertEqual(result["value"], {"sites": ["chatgpt.com", "openai.com"], "signed_in": True})
        self.assertEqual(len(self.served), 1, "the proxy started: the session had no sites")
        self.assertEqual(self.state.tunnels.sites, ["chatgpt.com", "openai.com"])
        written = self.home / ".codex/auth.json"
        self.assertEqual(json.loads(written.read_text()), json.loads(CODEX))
        if os.name != "nt":
            self.assertEqual(written.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("credentials", self.control.post("/poll", {}), "nothing renewed yet")
        # Codex renews its sign-in: the launcher hears it once.
        written.write_text('{"tokens": {"refresh_token": "new"}}')
        packet = self.control.post("/poll", {})
        self.assertEqual(packet["credentials"], [{"plan": "chatgpt", "text": '{"tokens": {"refresh_token": "new"}}'}])
        self.assertNotIn("credentials", self.control.post("/poll", {}))
        # A Claude plan adds its sites to the same proxy, and signs in with Claude Code's own
        # sign-in in this VM.
        thread, result, taken = self.asked({"plan": "claude", "use": "sign-in"})
        self.control.post("/subscription/granted", {"id": taken["id"], "sites": ["anthropic.com", "claude.ai"]})
        thread.join(10)
        self.assertEqual(result["value"]["signed_in"], False)
        self.assertEqual(self.state.tunnels.sites, ["chatgpt.com", "openai.com", "anthropic.com", "claude.ai"])
        self.assertEqual(len(self.served), 1)

    def test_a_claude_sign_in_never_travels(self):
        # Anthropic lets no third party collect, store or pass on Claude credentials: never from
        # the computer or Yougori's copy...
        for use in ("computer", "saved"):
            with self.assertRaisesRegex(ValueError, "Claude Code's own sign-in"):
                self.ask({"plan": "claude", "use": use})
        # ...never a sign-in the launcher sends...
        thread, result, taken = self.asked({"plan": "claude", "use": "sign-in"})
        with self.assertRaises(ValueError):
            self.control.post("/subscription/granted", {"id": taken["id"], "sites": ["anthropic.com"], "credential": SIGNED})
        self.assertFalse((self.home / ".claude/.credentials.json").exists())
        self.control.post("/subscription/granted", {"id": taken["id"], "sites": ["anthropic.com"]})
        thread.join(10)
        # ...and the one Claude Code makes here stays here.
        written = self.home / ".claude/.credentials.json"
        written.parent.mkdir(exist_ok=True)
        written.write_text(SIGNED)
        self.assertNotIn("credentials", self.control.post("/poll", {}))
        self.assertEqual(self.state.watched, {})

    def test_signing_in_again_takes_away_the_sign_in_this_vm_holds(self):
        written = self.home / ".claude/.credentials.json"
        written.parent.mkdir()
        written.write_text(SIGNED)
        # The one in this VM: it stays.
        thread, result, taken = self.asked({"plan": "claude", "use": "vm"})
        self.control.post("/subscription/granted", {"id": taken["id"], "sites": ["anthropic.com"]})
        thread.join(10)
        self.assertEqual(result["value"]["signed_in"], True)
        # Signing in again (another account): the client is asked for a new one.
        thread, result, taken = self.asked({"plan": "claude", "use": "sign-in"})
        self.control.post("/subscription/granted", {"id": taken["id"], "sites": ["anthropic.com"]})
        thread.join(10)
        self.assertEqual(result["value"]["signed_in"], False)
        self.assertFalse(written.exists())

    def test_a_refusal_reaches_the_screen_in_the_launchers_words(self):
        thread, result, taken = self.asked({"plan": "chatgpt", "use": "saved"})
        self.control.post("/subscription/failed", {"id": taken["id"], "reason": "Yougori no longer keeps a sign-in for this plan."})
        thread.join(10)
        self.assertEqual(result["error"], "Yougori no longer keeps a sign-in for this plan.")
        with self.assertRaises(ValueError):
            self.control.post("/subscription/granted", {"id": taken["id"], "sites": ["anthropic.com"]})

    def test_the_session_says_which_sign_ins_the_computer_holds(self):
        fields = {"harnesses": [{"id": "claude", "name": "Claude Code", "category": "coding", "description": "", "questions": [],
                                 "base": "base", "memory": 4, "ready": True}],
                  "vm_base": "base", "vm_memory": 4, "server": "https://argon.example"}
        data = {"harness": "claude", "answers": {}, "secrets": {}, "reach": {"mode": "inference"}, **fields,
                "subscriptions": {"claude": {"saved": True, "computer": False}, "chatgpt": {"computer": True}, "other": {}}}
        vm_guest.harness_session(data, "claude", {}, self.home, "token")
        # A Claude sign-in is never offered from the computer, whatever a launcher says.
        self.assertEqual(runtime.session(self.home)["subscriptions"], {"chatgpt": {"saved": False, "computer": True}})


class Launcher(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(folder.name)
        self.root = self.base / "InferenceSolver" / "abc"
        self.root.mkdir(parents=True)
        self.computer = self.base / "home/.codex/auth.json"
        patcher = patch.dict(os.environ, {"CODEX_HOME": str(self.computer.parent)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sent = []

    def post(self, path, data):
        self.sent.append((path, data))
        return {}

    def test_what_the_screen_is_offered(self):
        # Only a plan whose sign-in may travel: never Claude's.
        self.assertEqual(vm_host.subscriptions_state(self.root), {"chatgpt": {"saved": False, "computer": False}})
        self.computer.parent.mkdir(parents=True)
        self.computer.write_text(CODEX)
        self.assertEqual(vm_host.subscriptions_state(self.root), {"chatgpt": {"saved": False, "computer": True}})
        vm_host.write_whole(vm_host.kept_credential(self.root, "chatgpt"), "{}")
        self.computer.write_text("not json")
        self.assertEqual(vm_host.subscriptions_state(self.root), {"chatgpt": {"saved": True, "computer": False}})
        with self.assertRaises(ValueError):
            vm_host.computer_credential("claude")

    def test_the_sites_are_the_launchers_and_the_sign_in_goes_back_while_unchanged(self):
        control = type("Control", (), {"post": lambda _, path, data: self.post(path, data)})()
        plans = vm_host.Plans(self.root)
        tunnels = plans.grant(control, {"id": "1", "plan": "chatgpt", "use": "computer"}, None)
        self.assertEqual(self.sent[-1][0], "/subscription/failed", "this computer holds no sign-in")
        self.computer.parent.mkdir(parents=True)
        self.computer.write_text(CODEX)
        tunnels = plans.grant(control, {"id": "2", "plan": "chatgpt", "use": "computer", "sites": ["evil.example"]}, tunnels)
        path, data = self.sent[-1]
        self.assertEqual((path, data["sites"], data["credential"]), ("/subscription/granted", ["chatgpt.com", "openai.com"], CODEX))
        self.assertEqual(tunnels.sites, ["chatgpt.com", "openai.com"], "never the VM's own list")
        # A Claude plan: its sites, and never a sign-in of this computer's or a kept one.
        for use in ("computer", "saved"):
            tunnels = plans.grant(control, {"id": "3", "plan": "claude", "use": use}, tunnels)
            self.assertEqual(self.sent[-1][0], "/subscription/failed")
            self.assertIn("Claude Code's own sign-in", self.sent[-1][1]["reason"])
        tunnels = plans.grant(control, {"id": "4", "plan": "claude", "use": "sign-in"}, tunnels)
        self.assertNotIn("credential", self.sent[-1][1])
        self.assertEqual(tunnels.sites[-3:], ["anthropic.com", "claude.ai", "claude.com"])
        # Renewed in the VM: kept, and given back to this computer, whose file was as it was given.
        renewed = json.dumps({"auth_mode": "chatgpt", "tokens": {"access_token": "b", "refresh_token": "r2"}})
        plans.keep([{"plan": "chatgpt", "text": renewed}, {"plan": "chatgpt", "text": "[]"}, {"plan": "x", "text": "{}"}])
        self.assertEqual(vm_host.kept_credential(self.root, "chatgpt").read_text(), renewed)
        self.assertEqual(self.computer.read_text(), renewed)
        # This computer's Codex renewed it meanwhile: its own stays.
        self.computer.write_text(json.dumps({"tokens": {"refresh_token": "computer's"}}))
        plans.keep([{"plan": "chatgpt", "text": CODEX}])
        self.assertIn("computer's", self.computer.read_text())
        self.assertEqual(vm_host.kept_credential(self.root, "chatgpt").read_text(), CODEX)
        # A Claude sign-in a VM sends is never kept.
        plans.keep([{"plan": "claude", "text": SIGNED}])
        self.assertFalse(vm_host.kept_credential(self.root, "claude").exists())


if __name__ == "__main__":
    unittest.main()
