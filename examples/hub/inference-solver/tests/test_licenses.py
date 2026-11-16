"""What the harness VM images pass on under which licence (harness/NOTICE.md): each pack's licence
list (pack_licenses.py), the parts it leaves out or replaces, the harnesses not offered, the base
image's own notices (Containerfile.yougori, licenses/), and the screen binary's crate notices.

Run: python -I -B examples/hub/inference-solver/tests/test_licenses.py
"""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HARNESSES = Path(__file__).resolve().parents[1] / "site" / "harnesses"
SITE = HARNESSES.parent
SCRIPTS = SITE.parent / "scripts"
sys.path.insert(0, str(HARNESSES))
import pack_licenses  # noqa: E402

PACKAGES = json.loads((HARNESSES / "packages.json").read_text(encoding="utf-8"))
WEBSITE = json.loads((SITE / "harnesses.json").read_text(encoding="utf-8"))


def write(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class Pack(unittest.TestCase):
    """A pack laid out as the pack stage leaves it: tool/ (and source/) beside the script."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.site = self.root / "tool/lib/python3.12/site-packages"

    def dist(self, name, version, licence="", files=("LICENSE",), classifier=""):
        info = self.site / ("%s-%s.dist-info" % (name.replace("-", "_"), version))
        write(info / "METADATA", "Metadata-Version: 2.1\nName: %s\nVersion: %s\n%s%s" % (
            name, version, "License-Expression: %s\n" % licence if licence else "", "Classifier: License :: %s\n" % classifier if classifier else ""))
        for file in files:
            write(info / "licenses" / file, "Copyright (c) the %s authors\n" % name)

    def npm(self, name, version, licence=None, files=("LICENSE",), under="tool/node_modules"):
        folder = self.root / under / name
        write(folder / "package.json", json.dumps({"name": name, "version": version, **({"license": licence} if licence else {})}))
        for file in files:
            write(folder / file, "The %s licence\n" % name)

    def run_pack(self, spec, name="test"):
        with patch.dict(os.environ, {"TOOL_SPEC": json.dumps(spec)}):
            pack_licenses.main(name, root=self.root)
        return (self.root / "tool" / pack_licenses.OUT_NAME).read_text(encoding="utf-8")

    def test_every_package_is_listed_with_its_licence_and_the_texts_it_lacks_are_added(self):
        self.dist("carried", "1.0", "MIT")
        self.dist("bare-apache", "2.0", "Apache-2.0", files=())
        self.dist("bare-bsd", "3.0", files=(), classifier="OSI Approved :: BSD License")
        self.dist("choice", "4.0", "BSD-3-Clause OR GPL-2.0", files=())
        self.npm("left", "1.0.0", "MIT")
        self.npm("@scope/bare", "2.0.0", "ISC", files=())
        self.npm("nested", "3.0.0", "LGPL-3.0-or-later", files=(), under="tool/node_modules/left/node_modules")
        self.npm("in-source", "4.0.0", "MPL-2.0", files=(), under="source/node_modules")
        listed = self.run_pack({"kind": "pip", "version": "1"})
        for line in ("pip carried 1.0 | MIT", "pip bare-apache 2.0 | Apache-2.0", "npm @scope/bare 2.0.0 | ISC",
                     "npm nested 3.0.0 | LGPL-3.0-or-later", "npm in-source 4.0.0 | MPL-2.0"):
            self.assertIn(line, listed)
        # The standard text of each licence a package declares without carrying it.
        for text in ("Apache-2.0.txt, for: bare-apache 2.0", "BSD-3-Clause.txt, for: bare-bsd 3.0, choice 4.0", "ISC.txt, for: @scope/bare 2.0.0",
                     "LGPL-3.0.txt, for: nested 3.0.0", "GPL-3.0.txt, for: nested 3.0.0", "MPL-2.0.txt, for: in-source 4.0.0"):
            self.assertIn(text, listed)
        self.assertIn("Licensed under the Apache License, Version 2.0", listed)
        # Where the source of each copyleft part is; a BSD-or-GPL choice takes BSD.
        sources = listed.split("Complete source of the copyleft parts")[1].split("=" * 78 + "\n", 2)[1]
        self.assertIn("- nested 3.0.0: https://www.npmjs.com/package/nested/v/3.0.0", sources)
        self.assertIn("- in-source 4.0.0", sources)
        self.assertNotIn("choice", sources)
        self.assertIn("three years after this pack was published", listed)
        self.assertIn("npm nested 3.0.0 | LGPL-3.0-or-later | - | below (LGPL-3.0.txt, GPL-3.0.txt)\n", listed)

    def test_a_package_without_a_licence_stops_the_pack_unless_its_project_s_licence_is_named(self):
        self.npm("tool", "1.0.0", "Apache-2.0")
        self.npm("tool-linux-x64", "1.0.0", files=())
        with self.assertRaises(SystemExit) as stopped:
            self.run_pack({"kind": "npm", "version": "1"})
        self.assertIn("npm tool-linux-x64 1.0.0 (declares: nothing)", str(stopped.exception))
        listed = self.run_pack({"kind": "npm", "version": "1", "licenses": {"tool-linux-*": {"license": "Apache-2.0", "covered_by": "tool"}}})
        self.assertIn("npm tool-linux-x64 1.0.0 | Apache-2.0 | - | the project's licence, with tool: tool/node_modules/tool/LICENSE", listed)

    def test_what_may_not_be_passed_on_leaves_and_a_stand_in_takes_its_place(self):
        self.npm("cline", "3.0.0", "Apache-2.0")
        self.npm("@sap/xssec", "4.0.0", "SAP DEVELOPER LICENSE AGREEMENT")
        write(self.site / "pandasai/ee/skills/__init__.py", "enterprise code")
        write(self.site / "pandasai/ee/LICENSE", "The PandasAI Enterprise license")
        self.dist("pandasai", "3.0.0", "MIT")
        spec = {"kind": "npm", "version": "1",
                "drop": [{"path": "tool/node_modules/@sap/xssec", "why": "SAP's licence"},
                         {"path": "tool/node_modules/@sap/xsenv", "why": "absent", "optional": True}],
                "stand_in": [{"path": "tool/lib/python3.12/site-packages/pandasai/ee", "with": "pandasai-ee", "why": "enterprise"}]}
        listed = self.run_pack(spec)
        self.assertFalse((self.root / "tool/node_modules/@sap/xssec").exists())
        self.assertNotIn("@sap/xssec", listed.split("Index:")[1])
        self.assertIn("- Left out tool/node_modules/@sap/xssec: SAP's licence", listed)
        ee = self.site / "pandasai/ee"
        self.assertFalse((ee / "LICENSE").exists())
        self.assertIn("Yougori's stand-in", (ee / "skills/__init__.py").read_text(encoding="utf-8"))
        self.assertIn("Replaced tool/lib/python3.12/site-packages/pandasai/ee with Yougori's own stand-in", listed)
        with self.assertRaisesRegex(ValueError, "only tool/ and source/"):
            self.run_pack({"kind": "npm", "version": "1", "drop": [{"path": "../outside", "why": "x"}]})

    def test_a_release_executable_has_its_licence_and_notices(self):
        write(self.root / "tool/bin/grok", "binary")
        listed = self.run_pack(PACKAGES["grok"], "grok")
        self.assertIn("file tool/bin/grok | Apache-2.0 | SpaceXAI (xAI)", listed)
        self.assertIn("grok-build-THIRD-PARTY-NOTICES.txt, for: tool/bin/grok", listed)
        self.assertIn("THIRD-PARTY NOTICES", listed)


class Catalogue(unittest.TestCase):
    def test_a_harness_that_needs_permission_is_neither_listed_nor_built(self):
        listed = {item["id"] for item in WEBSITE}
        for name in ("claude", "strix"):
            self.assertNotIn(name, listed)
            self.assertIn("not offered", PACKAGES[name]["blocked"])
        self.assertEqual({name for name, spec in PACKAGES.items() if spec.get("blocked")}, {"claude", "strix"})
        # Every other pack in the catalogue, and every entry of its licence rules, stays buildable.
        for name, spec in PACKAGES.items():
            for rule in spec.get("licenses", {}).values():
                self.assertTrue((HARNESSES / "licenses/texts" / rule["text"]).is_file() if "text" in rule else rule.get("covered_by"), (name, rule))
                for notice in rule.get("notices", []):
                    self.assertTrue((HARNESSES / "licenses/texts" / notice).is_file(), notice)
            for entry in spec.get("stand_in", []):
                self.assertTrue((HARNESSES / "licenses/stand-ins" / entry["with"]).is_dir())

    def test_the_market_data_terms_are_said(self):
        described = {item["id"]: item["description"] for item in WEBSITE}
        for name in ("tradingagents", "finrobot"):
            self.assertIn("Yahoo Finance, whose terms allow personal use only", described[name])


class Images(unittest.TestCase):
    def test_the_base_image_keeps_every_licence_it_passes_on(self):
        containerfile = (HARNESSES / "Containerfile.yougori").read_text(encoding="utf-8")
        for stage in ("os", "postgres", "chromium"):
            self.assertIn("sh /usr/local/lib/yougori/debian-licenses.sh " + stage + " \\\n    && rm -rf /var/lib/apt/lists/* /usr/share/doc",
                          containerfile)
        self.assertIn("COPY --from=node /usr/local/LICENSE /usr/share/licenses/node/LICENSE", containerfile)
        self.assertIn("licenses/README.txt licenses/yougori-harness-crates.txt licenses/yougori-runtime-MIT.txt licenses/texts/AGPL-3.0.txt",
                      containerfile)
        self.assertIn('python3 installer.py "$HARNESS" \\\n    && python3 pack_licenses.py "$HARNESS"', containerfile)
        readme = (HARNESSES / "licenses/README.txt").read_text(encoding="utf-8")
        self.assertIn("for at least three years after this image was published", readme)
        self.assertIn("https://snapshot.debian.org/package/SOURCE/VERSION/", readme)
        self.assertEqual((HARNESSES / "licenses/yougori-runtime-MIT.txt").read_bytes(), (SITE.parent / "LICENSE").read_bytes())

    def test_the_screen_binarys_crate_notices_are_current(self):
        check = subprocess.run([sys.executable, "-I", str(SCRIPTS / "harness-notices.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

    @unittest.skipUnless(sys.platform.startswith("linux") and Path("/usr/bin/dpkg-query").exists(), "needs Debian's dpkg")
    def test_debian_licences_are_recorded_before_the_docs_go(self):
        with tempfile.TemporaryDirectory() as folder:
            script = (HARNESSES / "licenses/debian-licenses.sh").read_text(encoding="utf-8").replace("/usr/share/licenses/debian", folder)
            result = subprocess.run(["sh", "-c", script, "debian-licenses", "os"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((Path(folder) / "packages.tsv").read_text().strip())


if __name__ == "__main__":
    unittest.main()
