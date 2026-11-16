Licences of the software in this Yougori harness VM

This VM runs software from many authors, each part under its own licence. This folder says which,
where each licence text is, and how to get the complete source of every part whose licence asks for
it to be given.

What is here, and where its licence is

- Debian's packages (the operating system, and in some images PostgreSQL or Chromium): every
  installed package, its version and its source package are in /usr/share/licenses/debian/packages.tsv;
  the copyright record of each, with its licence, is in /usr/share/licenses/debian/copyright-*.txt.gz
  (zcat to read). The full texts of the licences those records name (GPL, LGPL, Apache, MPL and
  others) are in /usr/share/common-licenses.
- Python: its licence is /usr/local/lib/python3.12/LICENSE.txt.
- Node.js: its licence, with those of the parts it includes, is /usr/share/licenses/node/LICENSE.
- yougori-harness (/usr/local/bin/yougori-harness), the screen every harness runs in: Yougori's own
  program under the GNU Affero General Public License, version 3 only (AGPL-3.0.txt here). The notices
  of the crates it is built from are in yougori-harness-crates.txt here.
- Yougori's runtime files in /opt/solver (the relay, the VM's guest, the harness profiles, Yougori's
  terminal for agent frameworks and its kits): Yougori's inference-solver template, under the MIT
  licence in yougori-runtime-MIT.txt here.
- /opt/solver/tiktoken: the cl100k and o200k encodings of tiktoken (MIT, OpenAI), as tiktoken would
  fetch them, kept here because the VM reaches only its inference.
- The harness's own pack, in /opt/solver/tool (and /opt/solver/source for a harness built from its
  source): every package in it, its licence and the changes Yougori made are listed in
  /opt/solver/tool/THIRD-PARTY-LICENSES.txt.

The complete source

Yougori offers the complete corresponding source of every part of this VM whose licence asks for it
(the GNU GPL, LGPL and AGPL, the MPL and the like) and of yougori-harness:

- Yougori's own source (yougori-harness, the runtime files, the kits) is at
  https://github.com/you-gori/yougori, at the release this image was built for.
- Each Debian package's source, at the exact version packages.tsv names, is at
  https://snapshot.debian.org/package/SOURCE/VERSION/ (SOURCE and VERSION as packages.tsv gives them).
- Each Python and Node package's source is at the address its pack's THIRD-PARTY-LICENSES.txt names
  for it; a harness built from its source carries that source, with Yougori's changes, in
  /opt/solver/source.

And, for at least three years after this image was published, Yougori gives anyone who asks a copy
of that complete source, for no more than the cost of making and sending it: ask through
https://yougori.com/, naming the image (the harness and its version) and the part.

No warranty

The software in this VM comes without any warranty, as each part's licence says.
