"""Each person's own model APIs, for every harness and every key of theirs.

A person adds an OpenAI-compatible API on the public internet (OpenAI, OpenRouter, Groq, Together,
Mistral, DeepSeek, or Anthropic's or Google's OpenAI-compatible address): its name, its address, its
key, and, when they want, the models to use there and the context they serve. The website keeps the
key and never shows it again; a harness's VM never sees it either, since its requests reach the
website, which forwards them (gateway.py). The API's models join the person's own catalog (GET
/v1/models with their keys) as NAME/MODEL, beside the inference's, so `yougori inference run`
offers them and the Yougori harness's /model switches to them; Claude Code's and Codex's requests
reach them through the same translation as the inference's models (protocols.py).

Nothing is charged for them here: the person's provider bills them. Each request is recorded in
their usage, as their requests to the inference are. The website reaches only the API's addresses
on the public internet, never its own computer or network (upstream.connect `public`).
"""
import http.client
import json
import re
import secrets
import time

from upstream import UpstreamError, connect, public_address

APIS_PER_PERSON = 10
MODELS_PER_API = 100
# A catalog entry that is no chat model (embeddings, speech, images, moderation): left out of the
# models an API adds when the person names none.
NOT_CHAT = re.compile(r"embed|whisper|tts|dall-e|moderation|transcribe|realtime|audio|image|babbage|davinci|sora|rerank",
                      re.I)
MODEL = re.compile(r"[^\s/][^\s]{0,199}")
CONTEXTS = (1024, 2_000_000)
TABLE = """CREATE TABLE IF NOT EXISTS user_apis (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL, slug TEXT NOT NULL, url TEXT NOT NULL, secret TEXT NOT NULL, models TEXT NOT NULL,
    context INTEGER, created INTEGER NOT NULL, UNIQUE(user_id, slug))"""


class ApiInput(ValueError):
    """Words for the person who added the API."""


def slug(name):
    """The name as model ids carry it: lower case, letters, digits and dashes."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]


def wanted_models(text):
    """The models the person named (commas, spaces or new lines between them), each once."""
    if text is None or text == "":
        return []
    if not isinstance(text, str) or len(text) > 20000:
        raise ApiInput("Name at most %d models, separated by commas." % MODELS_PER_API)
    names = list(dict.fromkeys(part for part in re.split(r"[\s,]+", text) if part))
    if len(names) > MODELS_PER_API or not all(MODEL.fullmatch(name) for name in names):
        raise ApiInput("Name at most %d models, separated by commas, each as the API spells it." % MODELS_PER_API)
    return names


def context_of(value):
    if value is None or value == "":
        return None
    try:
        number = int(str(value).replace(",", "").replace("_", "").strip())
    except ValueError:
        raise ApiInput("Give the context window in tokens, such as 128000.") from None
    if not CONTEXTS[0] <= number <= CONTEXTS[1]:
        raise ApiInput("Give a context window from 1,024 to 2,000,000 tokens.")
    return number


def discover(url, key):
    """The API's chat models, as its catalog (GET /models) lists them: (id, context or None)."""
    connection, response = connect(url, key, "/models", timeout=15, public=True)
    try:
        raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError()
        data = json.loads(raw)["data"]
        if not isinstance(data, list):
            raise ValueError()
        found = []
        for item in data[:5000]:
            name = item.get("id") if isinstance(item, dict) else None
            if not isinstance(name, str) or not MODEL.fullmatch(name) or NOT_CHAT.search(name):
                continue
            context = next((value for value in map(item.get, ("context_length", "context_window", "max_model_len"))
                            if type(value) is int and CONTEXTS[0] <= value <= CONTEXTS[1]), None)
            found.append((name, context))
        return sorted(dict(found).items())
    except (ValueError, KeyError, TypeError, OSError, http.client.HTTPException):
        raise UpstreamError("The API did not return a model catalog. Name the models to use.", 502) from None
    finally:
        connection.close()


class Apis:
    def __init__(self, store):
        self.store = store
        with store.db() as db:
            db.execute(TABLE)

    def view(self, user_id):
        """The person's APIs, never their keys."""
        with self.store.db() as db:
            rows = db.execute("SELECT id,name,slug,url,models,context,created FROM user_apis WHERE user_id=? ORDER BY created",
                              (user_id,)).fetchall()
        return {"apis": [{"id": row["id"], "name": row["name"], "url": row["url"], "context": row["context"],
                          "created": row["created"], "models": [row["slug"] + "/" + model["id"] for model in json.loads(row["models"])]}
                         for row in rows]}

    def add(self, user_id, data):
        name = data.get("name")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40 or not slug(name):
            raise ApiInput("Give the API a name of 1 to 40 characters, with a letter or digit.")
        name = name.strip()
        try:
            url = public_address(data.get("url"))
        except ValueError as error:
            raise ApiInput(str(error)) from None
        key = data.get("key") or ""
        if not isinstance(key, str) or len(key) > 1024 or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise ApiInput("Paste the API key as the provider gave it.")
        context = context_of(data.get("context"))
        named = wanted_models(data.get("models"))
        if named:
            models = [{"id": model, "context": context} for model in named]
            # The address and key are checked with one request, so a typing error shows now.
            discover_check(url, key)
        else:
            try:
                found = discover(url, key)
            except UpstreamError as error:
                raise ApiInput(explained(error)) from None
            if not found:
                raise ApiInput("The API lists no chat models. Name the models to use.")
            if len(found) > MODELS_PER_API:
                raise ApiInput("The API lists %d models. Name the ones to use (at most %d), separated by commas."
                               % (len(found), MODELS_PER_API))
            models = [{"id": model, "context": context or found_context} for model, found_context in found]
        identity = secrets.token_hex(8)
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT COUNT(*) FROM user_apis WHERE user_id=?", (user_id,)).fetchone()[0] >= APIS_PER_PERSON:
                raise ApiInput("Remove an API before adding another: each person has at most %d." % APIS_PER_PERSON)
            if db.execute("SELECT 1 FROM user_apis WHERE user_id=? AND slug=?", (user_id, slug(name))).fetchone():
                raise ApiInput("You already have an API with this name. Choose another.")
            db.execute("INSERT INTO user_apis VALUES(?,?,?,?,?,?,?,?,?)",
                       (identity, user_id, name, slug(name), url, key, json.dumps(models), context, int(time.time())))
            self.store.audit(db, user_id, "api.added", identity)
        return {"id": identity, "models": [slug(name) + "/" + model["id"] for model in models]}

    def remove(self, user_id, data):
        with self.store.db() as db:
            gone = db.execute("DELETE FROM user_apis WHERE id=? AND user_id=?", (data.get("id"), user_id)).rowcount
            if gone:
                self.store.audit(db, user_id, "api.removed", str(data.get("id"))[:40])
        return {"removed": bool(gone)}

    def catalog(self, user_id):
        """The person's APIs' models as /v1/models lists them."""
        with self.store.db() as db:
            rows = db.execute("SELECT name,slug,models FROM user_apis WHERE user_id=? ORDER BY created", (user_id,)).fetchall()
        listed = []
        for row in rows:
            for model in json.loads(row["models"]):
                listed.append({"id": row["slug"] + "/" + model["id"], "object": "model", "owned_by": "api:" + row["name"],
                               "task": "text-generation", **({"context_length": model["context"]} if model.get("context") else {}),
                               "capabilities": {"structuredOutput": False}})
        return listed

    def route(self, user_id, model):
        """Where a request of the person's for `model` goes when it is one of their APIs' models:
        the address, the key and the model's name there. None otherwise."""
        if not isinstance(model, str) or "/" not in model:
            return None
        head, rest = model.split("/", 1)
        with self.store.db() as db:
            row = db.execute("SELECT id,url,secret,models FROM user_apis WHERE user_id=? AND slug=?", (user_id, head)).fetchone()
        if row is None or not any(entry["id"] == rest for entry in json.loads(row["models"])):
            return None
        return {"id": row["id"], "url": row["url"], "secret": row["secret"], "upstream": rest}


def explained(error):
    """What the person is told when the API did not answer as it should."""
    words = str(error)
    if "credential" in words:
        return "The API refused this key."
    if "could not be reached" in words:
        return "The API could not be reached at this address."
    return words


def discover_check(url, key):
    """One request to the API's catalog: its address and key answer. A catalog it cannot list is
    no failure here (the models were named); a refused key or an unreachable address is."""
    try:
        connection, _ = connect(url, key, "/models", timeout=15, public=True)
        connection.close()
    except UpstreamError as error:
        if "credential" in str(error) or "could not be reached" in str(error) or "public internet" in str(error):
            raise ApiInput(explained(error)) from None
