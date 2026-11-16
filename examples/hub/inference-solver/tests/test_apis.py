"""Each person's own model APIs (apis.py): added on the website with their key, listed with their
models in their own catalog only, forwarded there through the same chat, Responses and Messages
routes as the inference's models, recorded in their usage and charged nothing; reached only at
HTTPS addresses on the public internet.

Run: python -I -B examples/hub/inference-solver/tests/test_apis.py
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_service  # noqa: E402
from test_service import UPSTREAM_KEY  # noqa: E402
import apis  # noqa: E402
import gateway  # noqa: E402
import upstream  # noqa: E402

ADDRESS = "https://api.example.test/v1"


class Public(unittest.TestCase):
    def test_only_https_addresses_on_the_public_internet(self):
        self.assertEqual(upstream.public_address("https://api.example.test/v1beta/openai/"), "https://api.example.test/v1beta/openai")
        for bad in ("http://api.example.test/v1", "https://user:pass@api.example.test/v1", "https://api.example.test/v1?x=1",
                    "ftp://api.example.test", "https://api.example.test:99999/v1", "", None, "https://exa mple.test"):
            with self.assertRaises(ValueError, msg=bad):
                upstream.public_address(bad)
        for private in ("https://127.0.0.1/v1", "https://10.0.0.5/v1", "https://169.254.169.254/v1", "https://[::1]/v1",
                        "https://100.64.0.1/v1", "https://192.168.1.2/v1"):
            with self.assertRaises(upstream.UpstreamError, msg=private) as caught:
                upstream.connect(private, "", "/models", public=True)
            self.assertIn("public internet", str(caught.exception))


class OwnApis(test_service.Flow):
    def setUp(self):
        super().setUp()
        real = upstream.connect

        def local(base, secret, path, payload=None, timeout=30, expect=(200,), method=None, public=False):
            # The API "on the public internet" is this test's model server.
            self.assertTrue(public, "a person's API is reached only as a public one")
            self.assertEqual(base, ADDRESS)
            return real(self.model_url, secret, path, payload, timeout, expect, method)
        for module in (apis, gateway):
            patcher = patch.object(module, "connect", local)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.service.throttle = lambda peer: True

    def add(self, session, **fields):
        return self.call("/api/apis", {"name": "My provider", "url": ADDRESS, "key": UPSTREAM_KEY, **fields}, session)

    def test_a_person_adds_an_api_and_every_harness_can_use_it(self):
        self.endpoint()
        member = self.member()
        status, added, _ = self.add(member)
        self.assertEqual(status, 201, added)
        self.assertEqual(added["models"], ["my-provider/test/one", "my-provider/test/two"])
        status, view, _ = self.call("/api/apis", session=member)
        self.assertEqual(status, 200)
        self.assertEqual([(a["name"], a["url"], a["models"]) for a in view["apis"]], [("My provider", ADDRESS, added["models"])])
        self.assertNotIn(UPSTREAM_KEY, str(view), "the key is never shown again")
        # Their own catalog lists the API's models beside the inference's; nobody else's does.
        mine = self.key(session=member)["key"]
        status, listed, _ = self.call("/v1/models", key=mine)
        ids = [m["id"] for m in listed["data"]]
        self.assertEqual(ids, ["test/one", "test/two", "my-provider/test/one", "my-provider/test/two"])
        self.assertEqual(listed["data"][2]["owned_by"], "api:My provider")
        admin_key = self.key()["key"]
        self.assertNotIn("my-provider/test/one", [m["id"] for m in self.call("/v1/models", key=admin_key)[1]["data"]])
        status, body, _ = self.call("/v1/chat/completions", self.completion("my-provider/test/one"), key=admin_key)
        self.assertNotEqual(status, 200, "another person's API is not theirs to use")
        # Chat, streamed and whole, reaches the API with the model's name there.
        self.model.calls.clear()
        status, body, _ = self.call("/v1/chat/completions", self.completion("my-provider/test/one"), key=mine)
        self.assertEqual(status, 200, body)
        self.assertEqual((body["model"], body["choices"][0]["message"]["content"]), ("my-provider/test/one", "hello"))
        self.assertEqual(self.model.calls[-1]["model"], "test/one")
        status, raw, _ = self.call("/v1/chat/completions", self.completion("my-provider/test/two", stream=True), key=mine)
        self.assertEqual(status, 200)
        self.assertIn('"model": "my-provider/test/two"', raw)
        self.assertTrue(raw.rstrip().endswith("data: [DONE]"))
        # Claude Code's and Codex's routes reach it too.
        status, body, _ = self.call("/v1/messages", {"model": "my-provider/test/one", "max_tokens": 64,
                                                     "messages": [{"role": "user", "content": "hi"}]}, key=mine)
        self.assertEqual(status, 200, body)
        status, body, _ = self.call("/v1/responses", {"model": "my-provider/test/one", "input": "hi"}, key=mine)
        self.assertEqual(status, 200, body)
        # Recorded in their usage, and charged nothing here.
        status, usage, _ = self.call("/api/usage", session=member)
        own = [r for r in usage["requests"] if r["model"].startswith("my-provider/")]
        self.assertGreaterEqual(len(own), 4)
        self.assertTrue(all(r["status"] == "completed" and not r["cost_microusd"] for r in own), own)

    def test_named_models_their_context_and_the_limits(self):
        member = self.member()
        status, added, _ = self.add(member, name="Second one", models="test/one, extra-model\nextra-model", context="128,000")
        self.assertEqual(status, 201, added)
        self.assertEqual(added["models"], ["second-one/test/one", "second-one/extra-model"])
        mine = self.key(session=member)["key"]
        listed = {m["id"]: m for m in self.call("/v1/models", key=mine)[1]["data"]}
        self.assertEqual(listed["second-one/extra-model"]["context_length"], 128000)
        for fields, words in (({"name": "Second one"}, "already have an API with this name"),
                              ({"name": "!!!"}, "name of 1 to 40 characters"),
                              ({"key": "wrong-key", "name": "Third"}, "refused"),
                              ({"context": "12", "name": "Fourth"}, "1,024 to 2,000,000"),
                              ({"models": " ".join("m%d" % i for i in range(120)), "name": "Fifth"}, "at most 100 models"),
                              ({"url": "http://api.example.test/v1", "name": "Sixth"}, "HTTPS address")):
            status, body, _ = self.add(member, **fields)
            self.assertEqual(status, 400, body)
            self.assertIn(words, body["error"]["message"] if isinstance(body, dict) and "error" in body else str(body))
        # A catalog too long to take whole asks for the models by name.
        self.model.models = ["model-%d" % i for i in range(150)]
        status, body, _ = self.add(member, name="Big")
        self.assertEqual(status, 400)
        self.assertIn("lists 150 models", body["error"]["message"])
        # At most ten each.
        self.model.models = ["test/one"]
        for i in range(9):
            self.assertEqual(self.add(member, name="Api %d" % i)[0], 201)
        self.assertIn("at most 10", self.add(member, name="Eleventh")[1]["error"]["message"])
        # Removed: its models leave the catalog. Another person cannot remove it.
        view = self.call("/api/apis", session=member)[1]["apis"]
        first = next(a for a in view if a["name"] == "Second one")
        self.assertEqual(self.call("/api/apis/remove", {"id": first["id"]}, self.admin)[1], {"removed": False})
        self.assertEqual(self.call("/api/apis/remove", {"id": first["id"]}, member)[1], {"removed": True})
        self.assertNotIn("second-one/test/one", [m["id"] for m in self.call("/v1/models", key=mine)[1]["data"]])

    def test_an_api_that_takes_no_stream_options_is_asked_again_without(self):
        member = self.member()
        self.assertEqual(self.add(member)[0], 201)
        mine = self.key(session=member)["key"]
        real = upstream.connect
        bodies = []

        def picky(base, secret, path, payload=None, timeout=30, expect=(200,), method=None, public=False):
            bodies.append(payload)
            if payload and "stream_options" in payload:
                payload = {**payload, "messages": [{"role": "user", "content": "refuse"}]}
            return real(self.model_url, secret, path, payload, timeout, expect, method)
        with patch.object(gateway, "connect", picky):
            status, body, _ = self.call("/v1/chat/completions", self.completion("my-provider/test/one"), key=mine)
        self.assertEqual(status, 200, body)
        self.assertEqual(["stream_options" in b for b in bodies], [True, False])


# Only this file's own tests: the shared fixture's are test_service's.
for name in dir(test_service.Flow):
    if name.startswith("test_") and name not in OwnApis.__dict__:
        setattr(OwnApis, name, None)


if __name__ == "__main__":
    unittest.main()
