"""A disposable service for browser checks; ends when its parent's stdin closes or sends an empty line.

Lines it reads before then change what it serves, each answered with one line:
`unpublish NAME...` removes those harnesses' published VM images (every harness's without a name), as
before the administrator publishes them, and answers `unpublished`; `publish` publishes every image
again and answers `published`; `offline` points every model server at an address nothing answers
(127.0.0.1:9) and marks it Unavailable, as a server that went away, and answers `offline`; `online`
puts them back, ready, and answers `online`.
"""
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_service import Flow
from artifact_fixture import publish_catalog_fixtures, unpublish_catalog_fixtures
import harness_download
import neocloud

# Rent a cloud GPU asks no real provider: this address answers nothing, so the page says so.
for name in ("RUNPOD_GRAPHQL", "RUNPOD_REST", "VAST", "HUGGING_FACE"):
    setattr(neocloud, name, "http://127.0.0.1:9")

fixture = Flow()
if len(sys.argv) > 1:
    fixture.account_mode = sys.argv[1]
try:
    fixture.setUp()
    publish_catalog_fixtures(fixture.temp.name)
    fixture.endpoint()
    print(fixture.url, flush=True)
    moved = {}
    for line in iter(sys.stdin.readline, ""):
        words = line.split()
        if not words:
            break
        if words[0] == "unpublish":
            catalog = harness_download.catalog()
            for name in words[1:]:
                if name not in catalog:
                    raise SystemExit(f"No harness {name} to unpublish.")
            unpublish_catalog_fixtures(fixture.temp.name, words[1:])
            print("unpublished", flush=True)
        elif words[0] == "publish":
            publish_catalog_fixtures(fixture.temp.name)
            print("published", flush=True)
        elif words[0] in ("offline", "online"):
            # A refresh in between finds the same: nothing answers at 127.0.0.1:9, and the model answers again.
            with fixture.service.store.db() as db:
                if words[0] == "offline":
                    moved = {r["id"]: r["url"] for r in db.execute("SELECT id,url FROM endpoints")}
                    db.executemany("UPDATE endpoints SET url='http://127.0.0.1:9/v1',state='unavailable',checked=? WHERE id=?",
                                   [(int(time.time()), id) for id in moved])
                else:
                    db.executemany("UPDATE endpoints SET url=?,state='ready',checked=? WHERE id=?",
                                   [(url, int(time.time()), id) for id, url in moved.items()])
            print(words[0], flush=True)
        else:
            raise SystemExit(f"Unknown fixture command {words[0]}.")
finally:
    fixture.tearDown()
