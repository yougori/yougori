"""The notices of every crate the Yougori harness's screen binary (yougori-harness) is built from, for
its VM images: each crate of harness/Cargo.lock taken from the repository's rendered notices
(src-tauri/resources/APPLICATION_LICENSES.txt, which the compliance tooling keeps complete for every
lockfile) into site/harnesses/licenses/yougori-harness-crates.txt. --check only compares.

Usage: python -I harness-notices.py [--check]
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
LOCK = REPO / "harness" / "Cargo.lock"
RENDERED = REPO / "src-tauri" / "resources" / "APPLICATION_LICENSES.txt"
OUT = Path(__file__).resolve().parents[1] / "site" / "harnesses" / "licenses" / "yougori-harness-crates.txt"
RULE = "=" * 72
HEADER = """Notices of the crates the Yougori harness's screen (/usr/local/bin/yougori-harness) is built from

yougori-harness itself is Yougori's own program under the GNU Affero General Public License,
version 3 (AGPL-3.0-only, the text beside this file); its source and Yougori's offer of it are in
README.txt here. It is built from these crates (harness/Cargo.lock), each under its own licence,
whose notice follows. The list keeps every locked crate, build-only and other platforms' included.
"""


def locked():
    """(name, version) of every registry crate in harness/Cargo.lock (the harness itself aside)."""
    crates, name, version, source, package = [], None, None, None, False
    for line in LOCK.read_text(encoding="utf-8").splitlines() + ["[[package]]"]:
        if line == "[[package]]":
            if name and source:
                crates.append((name, version))
            name = version = source = None
            package = True
        elif not package:
            continue  # The lockfile's own header (its format version).
        elif line.startswith("name = "):
            name = line.split('"')[1]
        elif line.startswith("version = "):
            version = line.split('"')[1]
        elif line.startswith("source = "):
            source = line.split('"')[1]
    return sorted(set(crates))


def sections():
    """Each rendered cargo section of the repository's notices, by (name, version)."""
    text = RENDERED.read_text(encoding="utf-8")
    found = {}
    for part in re.split(r"(?m)^={72}\n", text):
        head = part.split("\n", 1)[0]
        if head.startswith("cargo: "):
            name, _, version = head[len("cargo: "):].partition(" ")
            found[(name, version.strip())] = part.rstrip("\n") + "\n"
    return found


def render():
    known = sections()
    missing = [crate for crate in locked() if crate not in known]
    if missing:
        raise SystemExit("No rendered notice for: " + ", ".join("%s %s" % crate for crate in missing)
                         + ". Refresh src-tauri/resources/APPLICATION_LICENSES.txt with the compliance tooling first.")
    return HEADER + "".join("\n" + RULE + "\n" + known[crate] for crate in locked())


def main():
    text = render()
    if "--check" in sys.argv[1:]:
        if not OUT.is_file() or OUT.read_text(encoding="utf-8") != text:
            raise SystemExit(str(OUT) + " is not current: run scripts/harness-notices.py.")
        print("yougori-harness crate notices are current (%d crates)." % len(locked()))
        return
    OUT.write_bytes(text.encode("utf-8"))
    print("Wrote %s: %d crates, %d bytes." % (OUT, len(locked()), len(text)))


if __name__ == "__main__":
    main()
