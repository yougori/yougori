"""Build-time only (Containerfile.yougori's pack stage, after installer.py): a pack's licences.

1. Leaves out what may not be passed on, or is unused and carries no licence (the pack spec's
   "drop", each with why), and puts Yougori's own stand-in where a harness's core imports code that
   may not be passed on ("stand_in": licenses/stand-ins/NAME in its place).
2. Writes tool/THIRD-PARTY-LICENSES.txt: the changes Yougori made (also the installer's own,
   "modified", which a copyleft licence asks to be dated); every Python distribution, Node package
   and Go module in the pack, with the licence it declares, its authors and where its licence text
   is; the standard text of every licence a package declares without carrying it (licenses/texts),
   or the rule in the spec's "licenses" for one that declares none (the text of its project, carried
   by another package, or a standard text); the files that are no package (a release's executable,
   licenses with a "path"); and where the complete source of each copyleft part is.

A package with neither a licence text nor a licence the list can name stops the build, which says
which: its licence has to be found (and named in "licenses") before the pack is passed on.

Usage: python3 pack_licenses.py HARNESS   (the pack spec comes from TOOL_SPEC, as for installer.py)
"""
import email.parser
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
TEXTS = HERE / "licenses" / "texts"
STAND_INS = HERE / "licenses" / "stand-ins"
OUT_NAME = "THIRD-PARTY-LICENSES.txt"
LICENCE_FILE = re.compile(r"(?i)^(licen[cs]e|copying|notice|copyright|unlicense|third[-_]party[-_]notices)(\..*|[-_].*)?$")
COPYLEFT = re.compile(r"(?i)\b(AGPL|LGPL|GPL|MPL|EPL|CDDL|EUPL|Affero|General Public|Lesser|Mozilla Public|Eclipse Public)")
# The standard texts for the licences packages declare, by what the declaration says. An "or"
# choice keeps the permissive option's text; "and" keeps each.
STANDARD = [
    (re.compile(r"(?i)\bAGPL|Affero"), ["AGPL-3.0.txt"]),
    (re.compile(r"(?i)\bLGPL[- ]?v?3|Lesser General Public License v3|LGPL-3"), ["LGPL-3.0.txt", "GPL-3.0.txt"]),
    (re.compile(r"(?i)\bLGPL[- ]?v?2|Lesser General Public License v2|Library or Lesser|\bLGPL\b(?![- ]?v?3)"), ["LGPL-2.1.txt"]),
    (re.compile(r"(?i)(?<!L)GPL[- ]?v?3|General Public License v3|(?<!L)GPL-3"), ["GPL-3.0.txt"]),
    (re.compile(r"(?i)(?<!L)GPL[- ]?v?2|General Public License v2|(?<!L)GPL-2"), ["GPL-2.0.txt"]),
    (re.compile(r"(?i)\bMPL|Mozilla Public"), ["MPL-2.0.txt"]),
    (re.compile(r"(?i)Elastic"), ["Elastic-2.0.txt"]),
    (re.compile(r"(?i)\bApache"), ["Apache-2.0.txt"]),
    (re.compile(r"(?i)\bMIT\b"), ["MIT.txt"]),
    (re.compile(r"(?i)BSD[- ]?2"), ["BSD-2-Clause.txt"]),
    (re.compile(r"(?i)BSD(?![- ]?2)"), ["BSD-3-Clause.txt"]),
    (re.compile(r"(?i)\bISC\b"), ["ISC.txt"]),
    (re.compile(r"(?i)Unlicense"), ["Unlicense.txt"]),
]
PUBLIC_DOMAIN = re.compile(r"(?i)public domain|CC0")
RULE = "=" * 78


def canonical(name):
    return re.sub(r"[-_.]+", "-", str(name)).lower()


def spec_of(name):
    if os.getenv("TOOL_SPEC"):
        return json.loads(os.environ["TOOL_SPEC"])
    return json.loads((HERE / "packages.json").read_text(encoding="utf-8"))[name]


def inside(root, relative):
    """`relative` (a pack path such as tool/node_modules/@sap/xssec) under `root`, never outside."""
    parts = PurePosixPath(relative).parts
    if not parts or parts[0] not in ("tool", "source") or ".." in parts or PurePosixPath(relative).is_absolute():
        raise ValueError("A licence change may touch only tool/ and source/: " + relative)
    return root.joinpath(*parts)


def remove(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def apply_changes(root, spec):
    """The spec's "drop" and "stand_in" entries applied, and its "modified" notes: what was done."""
    done = []
    for entry in spec.get("drop", []):
        matches = sorted(root.glob(entry["path"])) if any(c in entry["path"] for c in "*?[") else [inside(root, entry["path"])]
        matches = [path for path in matches if path.exists() or path.is_symlink()]
        if not matches:
            if entry.get("optional"):
                continue
            raise ValueError("Nothing to leave out at " + entry["path"] + ": review the pack's licence changes.")
        for path in matches:
            inside(root, path.relative_to(root).as_posix())
            remove(path)
            done.append("Left out " + path.relative_to(root).as_posix() + ": " + entry["why"])
    for entry in spec.get("stand_in", []):
        path = inside(root, entry["path"])
        if not path.exists():
            raise ValueError("No " + entry["path"] + " to stand in for: review the pack's licence changes.")
        remove(path)
        shutil.copytree(STAND_INS / entry["with"], path)
        done.append("Replaced " + entry["path"] + " with Yougori's own stand-in (licenses/stand-ins/" + entry["with"]
                    + " in Yougori's source): " + entry["why"])
    for note in spec.get("modified", []):
        done.append("Modified by Yougori: " + note)
    return done


def text_files(folder, recursive=False):
    """Licence-like files of a folder (its own, or below it when recursive)."""
    found = []
    if not folder.is_dir():
        return found
    for path in sorted(folder.rglob("*") if recursive else folder.iterdir()):
        if path.is_file() and not path.is_symlink() and LICENCE_FILE.match(path.name):
            found.append(path)
    return found


def python_packages(root):
    """Every distribution in the pack's Python environment: name, version, licence, authors, files."""
    found = []
    folders = sorted(root.glob("tool/lib/python*/site-packages/*.dist-info")) + sorted(root.glob("tool/lib/python*/site-packages/*.egg-info"))
    for info in folders:
        metadata = info / ("METADATA" if info.suffix == ".dist-info" else "PKG-INFO")
        if not metadata.is_file():
            continue
        message = email.parser.Parser().parsestr(metadata.read_text(encoding="utf-8", errors="replace"), headersonly=True)
        declared = [(message.get("License-Expression") or "").strip()]
        licence = (message.get("License") or "").strip()
        declared.append(licence.splitlines()[0][:120] if licence else "")
        declared += [c.split("::")[-1].strip() for c in message.get_all("Classifier") or [] if c.startswith("License ::")]
        declared = "; ".join(dict.fromkeys(d for d in declared if d and d.upper() not in ("UNKNOWN", "NONE")))
        authors = "; ".join(dict.fromkeys(a.strip() for a in (message.get("Author"), message.get("Author-email"), message.get("Maintainer"))
                                          if a and a.strip() and a.strip().upper() != "UNKNOWN"))
        found.append({"ecosystem": "pip", "name": message.get("Name") or info.name.split("-")[0], "version": message.get("Version") or "",
                      "declared": declared, "authors": authors[:200], "files": text_files(info, recursive=True)})
    return found


def node_packages(root):
    """Every Node package in the pack, nested ones and those of a kept source tree too."""
    found = []
    for manifest in sorted(list(root.glob("tool/**/node_modules/**/package.json")) + list(root.glob("source/**/node_modules/**/package.json"))):
        folder = manifest.parent
        parent = folder.parent
        # A package's own package.json: node_modules/NAME or node_modules/@scope/NAME.
        if not (parent.name == "node_modules" or (parent.name.startswith("@") and parent.parent.name == "node_modules")):
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8", errors="replace"))
        except ValueError:
            continue
        if not isinstance(data, dict) or not isinstance(data.get("name"), str):
            continue
        licence = data.get("license")
        if isinstance(licence, dict):
            licence = licence.get("type")
        if licence is None and isinstance(data.get("licenses"), list):
            licence = " OR ".join(str(item.get("type") if isinstance(item, dict) else item) for item in data["licenses"])
        author = data.get("author")
        author = author.get("name") if isinstance(author, dict) else author
        found.append({"ecosystem": "npm", "name": data["name"], "version": str(data.get("version", "")),
                      "declared": licence if isinstance(licence, str) else "", "authors": str(author or "")[:200],
                      "files": text_files(folder)})
    return found


def go_modules():
    """The Go modules a build of the pack's Go programs fetched (its module cache, which the pack
    stage removes after this), with their licence files, and Go's own library: they are compiled
    into those programs, so their notices go with the pack."""
    cache = Path(os.environ.get("GOMODCACHE") or Path.home() / "go/pkg/mod")
    found = []
    if cache.is_dir():
        for folder in sorted(cache.rglob("*@*")):
            relative = folder.relative_to(cache).as_posix()
            if not folder.is_dir() or relative.startswith("cache/") or "@" in relative.split("@", 1)[0].rsplit("/", 1)[0] \
                    or relative.count("@") != 1 or "/" in relative.split("@", 1)[1]:
                continue
            module, version = relative.split("@", 1)
            found.append({"ecosystem": "go", "name": module, "version": version, "declared": "", "authors": "",
                          "files": text_files(folder), "embed": True})
    for licence in sorted(Path("/usr/lib").glob("go*/LICENSE"))[:1]:
        found.append({"ecosystem": "go", "name": "Go standard library and runtime", "version": licence.parent.name, "declared": "BSD-3-Clause",
                      "authors": "The Go Authors", "files": [licence], "embed": True})
    return found


def standard(declared):
    """The standard texts for what a package declares, or None when it names no licence they know."""
    if not declared:
        return None
    if PUBLIC_DOMAIN.search(declared) and not any(pattern.search(declared) for pattern, _ in STANDARD):
        return []
    choice = re.split(r"(?i)\s+OR\s+", declared)
    if len(choice) > 1:
        # An "or" choice: the first permissive option.
        for option in choice:
            texts = standard(option)
            if texts and not COPYLEFT.search(option):
                return texts
    texts = []
    for pattern, names in STANDARD:
        if pattern.search(declared):
            texts += [name for name in names if name not in texts]
    return texts or None


def copyleft(declared):
    """Whether a declared licence binds the pack to give source: a copyleft one, unless an "or"
    choice offers a permissive one, which the pack takes."""
    options = re.split(r"(?i)\s+OR\s+|\s*/\s*", declared or "")
    return bool(COPYLEFT.search(declared or "")) and all(COPYLEFT.search(option) for option in options if option.strip())


def rule_for(package, licences):
    name = canonical(package["name"])
    for key, value in licences.items():
        if "path" in value:
            continue
        if canonical(key) == name or (key.endswith("*") and name.startswith(canonical(key[:-1]))):
            return value
    return None


def source_of(package, spec):
    """Where a package's complete source is, for the written offer."""
    if package["ecosystem"] == "pip":
        return "https://pypi.org/project/%s/%s/#files (its source distribution)" % (package["name"], package["version"])
    if package["ecosystem"] == "npm":
        return "https://www.npmjs.com/package/%s/v/%s (the package as published)" % (package["name"], package["version"])
    if package["ecosystem"] == "go":
        return "https://proxy.golang.org/%s/@v/%s.zip" % (package["name"].lower(), package["version"])
    return package.get("source") or ""


def write_list(root, name, spec, changes, packages, extra):
    lines = [
        "Third-party software in the Yougori harness's pack for %s (%s %s)" % (name, spec.get("kind"), spec.get("version", "")),
        "",
        "This pack holds the harness and every package it installs, each under its own licence, which",
        "the index names with the package's authors and where its licence text is: beside the package,",
        "or at the end of this file. Yougori's own files and its offer of the source of every copyleft",
        "part are in /usr/share/licenses/yougori in the VM.",
        "",
    ]
    if changes:
        lines += ["Changes Yougori made to what the harness's installer installed:", *("- " + change for change in changes), ""]
    lines += [RULE, "Index: kind, package, version | licence as declared | authors | its licence text", RULE]
    sources, texts, embedded = [], {}, {}
    for package in sorted(packages, key=lambda p: (p["ecosystem"], canonical(p["name"]), p["version"])):
        if package.get("embed"):
            where = ", ".join("below (%s)" % hashlib.sha256(path.read_bytes()).hexdigest()[:12] for path in package["files"]) \
                or "no licence file in the module"
            for path in package["files"]:
                embedded.setdefault(hashlib.sha256(path.read_bytes()).hexdigest(), (path, []))[1].append(package["name"] + " " + package["version"])
        else:
            where = ", ".join(path.relative_to(root).as_posix() for path in package["files"][:4]) or package.get("covered", "")
            if package["files"] and package.get("covered"):
                where += "; " + package["covered"]
        lines.append("%s %s %s | %s | %s | %s" % (package["ecosystem"], package["name"], package["version"], package["declared"] or "-",
                                                 package.get("authors") or "-", where))
        for text in package.get("texts", []):
            texts.setdefault(text, []).append("%s %s" % (package["name"], package["version"]))
        if copyleft(package["declared"]):
            sources.append(package)
    for item in extra:
        lines.append("file %s | %s | %s | %s" % (item["path"], item["license"], item.get("authors", "-"), item["covered"]))
        for text in item["texts"]:
            texts.setdefault(text, []).append(item["path"])
        if copyleft(item["license"]):
            sources.append({"ecosystem": "file", "name": item["path"], "version": "", "source": item.get("source", "")})
    lines += ["", RULE, "Complete source of the copyleft parts (GPL, LGPL, AGPL, MPL and the like)", RULE,
              "Each is the package as published at this version unless a change above names it, and its",
              "complete corresponding source is at the address given. Yougori also gives it on request, for",
              "three years after this pack was published, for no more than the cost of copying it: see",
              "/usr/share/licenses/yougori/README.txt in the VM."]
    lines += ["- %s %s: %s" % (p["name"], p["version"], source_of(p, spec)) for p in sources] or ["- none in this pack"]
    for text, users in sorted(texts.items()):
        lines += ["", RULE, "%s, for: %s" % (text, ", ".join(users)), RULE, (TEXTS / text).read_text(encoding="utf-8").rstrip()]
    for digest, (path, users) in sorted(embedded.items()):
        lines += ["", RULE, "%s, for: %s" % (digest[:12], ", ".join(users)), RULE, path.read_text(encoding="utf-8", errors="replace").rstrip()]
    (root / "tool" / OUT_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(name, root=HERE):
    spec = spec_of(name)
    changes = apply_changes(root, spec)
    licences = spec.get("licenses", {})
    packages = python_packages(root) + node_packages(root)
    if spec.get("kind") in ("plandex", "pentagi"):
        packages += go_modules()
    by_name = {}
    for package in packages:
        if package["files"]:
            by_name.setdefault(canonical(package["name"]), package)
    missing = []
    for package in packages:
        rule = rule_for(package, licences)
        if rule:
            # The rule names the licence, and where its text is when the package does not carry it
            # (or carries less than the licence: a one-line statement).
            package["declared"] = package["declared"] or rule["license"]
            if rule.get("authors"):
                package["authors"] = rule["authors"]
            if rule.get("covered_by"):
                covering = by_name.get(canonical(rule["covered_by"]))
                if not covering:
                    missing.append("%s %s: %s carries no licence text" % (package["ecosystem"], package["name"], rule["covered_by"]))
                    continue
                package["covered"] = "the project's licence, with %s: %s" % (covering["name"], covering["files"][0].relative_to(root).as_posix())
            if rule.get("text"):
                package["texts"] = [rule["text"]]
                package["covered"] = "below (%s)" % rule["text"]
            if not (rule.get("covered_by") or rule.get("text")):
                missing.append("%s %s: its rule names no text" % (package["ecosystem"], package["name"]))
            continue
        if package["files"]:
            continue
        if package["ecosystem"] == "go":
            print("No licence file in Go module %s %s." % (package["name"], package["version"]), flush=True)
            continue
        texts = standard(package["declared"])
        if texts is None:
            missing.append("%s %s %s (declares: %s)" % (package["ecosystem"], package["name"], package["version"], package["declared"] or "nothing"))
            continue
        package["texts"] = texts
        package["covered"] = "below (%s)" % ", ".join(texts) if texts else "public domain, as it says"
    for text in {text for package in packages for text in package.get("texts", [])}:
        if not (TEXTS / text).is_file():
            raise ValueError("No licence text licenses/texts/" + text)
    # Files that are no package of an ecosystem (a release's executable): "licenses" entries with a
    # "path" in the pack.
    extra = []
    for rule in licences.values():
        if "path" not in rule:
            continue
        if not inside(root, rule["path"]).exists():
            raise ValueError("No " + rule["path"] + " in the pack, which its licence entry names.")
        texts = [rule["text"], *rule.get("notices", [])]
        for text in texts:
            if not (TEXTS / text).is_file():
                raise ValueError("No licence text licenses/texts/" + text)
        extra.append({"path": rule["path"], "license": rule["license"], "texts": texts, "source": rule.get("source", ""),
                      "authors": rule.get("authors", "-"),
                      "covered": "below (%s)" % ", ".join(texts) + ("; source: " + rule["source"] if rule.get("source") else "")})
    if missing:
        raise SystemExit("These packages of %s carry no licence text and the pack names none for them (packages.json "
                         "\"licenses\"):\n  %s" % (name, "\n  ".join(sorted(set(missing)))))
    write_list(root, name, spec, changes, packages, extra)
    digest = hashlib.sha256((root / "tool" / OUT_NAME).read_bytes()).hexdigest()[:12]
    print("%s: %d packages, %d files, %d changes; licence list %s" % (name, len(packages), len(extra), len(changes), digest), flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
