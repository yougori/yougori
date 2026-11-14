"""How the security harnesses' own terminals look inside the Yougori screen's pane.

HexStrike, PentAGI and Shannon have terminals of ours (hexstrike_cli.py, pentagi_cli.py,
shannon_cli.py); PentestGPT and Strix bring their own, with a few lines of ours around them. All of
them draw with this module, in the look of the screen itself (harness/src/ui): Yougori's colours
and wordmark, the person's turns after `›`, Markdown as the screen draws a message, each step on a
row that turns while it runs and then says how it went and how long it took, and the screen's input
box, whose line edits as in any terminal program: the arrows, Delete, Home and End, a word at a
time, history, paste. A key the line has no use for never shows up in it as a code.

Standard library only, so every Python of the images runs it. Without a terminal (a pipe, the
tests) it writes plain lines and reads with input().

Inside the Yougori screen's pane (YOUGORI_PANE=1) the input box also tells the pane whether its line
is empty, so a `/` typed at its start opens Yougori's own commands (/harness, /model, /new, /help)
and one typed after text stays the harness's; and a hangup (the pane stopping the harness for /new
or another model) ends the harness as Ctrl+C at its prompt does, so what it started stops with it.

`python3 terminal_ui.py brief TEXT -- PROGRAM ARGS...` shows TEXT as the line to paste at a
harness's first question (PentestGPT), then runs the program in its place."""
import codecs
import contextlib
import os
import re
import select
import signal
import sys
import threading
import time
import unicodedata

try:
    import termios
except ImportError:  # not a Unix terminal: plain lines only
    termios = None

# Yougori's colours (harness/src/ui/theme.rs).
BLUE = (0x28, 0x6E, 0xEA)
SKY = (0x38, 0xBD, 0xF8)
GREEN = (0x4A, 0xDE, 0x80)
AMBER = (0xFB, 0xBF, 0x24)
RED = (0xF8, 0x71, 0x71)
GRAY = (0x71, 0x71, 0x7A)
PALE = (0x9C, 0xC2, 0xFF)
WHITE = (0xFF, 0xFF, 0xFF)
# The screen's text, and the text under the light that runs over a step under way (dimmer, so the
# light shows).
TEXT = (0xE4, 0xE4, 0xE7)
SOFT = (0xA8, 0xA8, 0xB2)
# Each security harness's own pair of colours, from the first to its signature (the second): its
# name, its headings, the `›` of its turns, its input box and the light over its work. Any other
# harness has the Yougori screen's own blue to sky.
BRANDS = {
    "hexstrike": ((0xFF, 0x6B, 0x6B), (0xFF, 0xA9, 0x4D)),   # coral to orange
    "pentagi": ((0xF7, 0x83, 0xAC), (0xB1, 0x97, 0xFC)),     # pink to lavender
    "shannon": ((0x74, 0xC0, 0xFC), (0x38, 0xD9, 0xA9)),     # light blue to mint
    "strix": ((0xFF, 0x92, 0x2B), (0xFF, 0xD4, 0x3B)),       # orange to yellow
    "pentestgpt": ((0x69, 0xDB, 0x7C), (0x3B, 0xC9, 0xDB)),  # green to cyan
}
# The light: how fast it runs over a line (columns a second), how wide it is, and the pause
# between two passes; the input box's colours go once round in FLOW_SECONDS.
LIGHT_SPEED, LIGHT_WIDTH, LIGHT_PAUSE = 22.0, 4.0, 0.5
FLOW_SECONDS = 4.0
# The input box is drawn again this often while it waits, as its colours move (its border in steps
# of FLOW_STEP columns, each a colour): a few kilobytes a second, little for any terminal.
FLOW_TICK, FLOW_STEP = 0.25, 6
# The light over the banner as it shows: columns a frame, and seconds a frame (under a second).
SWEEP_STEP, SWEEP_FRAME = 2.0, 0.025


def brand(name=None):
    """The pair of colours of the harness `name`, or of the one this VM runs (SOLVER_HARNESS)."""
    for given in (name, os.environ.get("SOLVER_HARNESS")):
        key = re.sub(r"[^a-z]", "", (given or "").lower())
        for known, pair in BRANDS.items():
            if key and key.startswith(known):
                return pair
    return BLUE, SKY
# The braille spinner the screen turns while work runs, a frame every 80 ms; its colour goes from
# the harness's first colour to its signature and back in SPIN_COLOUR_SECONDS.
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPIN_SECONDS = 0.08
SPIN_COLOUR_SECONDS = 1.6
BOLD, DIM, ITALIC, UNDERLINE, STRIKE = "1", "2", "3", "4", "9"
# The width of a step's kind, as the screen's tool rows ("Edit   ", "Search ").
KIND = 7
# The width of plain lines (no terminal): nothing is wrapped, rules are as wide as RULE_WIDTH and a
# row's end follows its start.
PLAIN = 10 ** 4
RULE_WIDTH = 80


def color_depth(environ=None):
    """24 for true colour, 8 for the 256-colour palette, 0 for none (NO_COLOR), as the pane tells
    its client (harness/src/ui/pane.rs client_env)."""
    environ = os.environ if environ is None else environ
    if environ.get("NO_COLOR") or environ.get("TERM") == "dumb":
        return 0
    return 24 if environ.get("COLORTERM", "").lower() in ("truecolor", "24bit") else 8


def nearest_256(rgb):
    """The nearest colour of the 256-colour palette's cube and gray ramp (theme.rs nearest_256)."""
    levels = (0, 95, 135, 175, 215, 255)
    index = [min(range(6), key=lambda i: abs(levels[i] - value)) for value in rgb]
    cube = tuple(levels[i] for i in index)
    step = min(max(sum(rgb) // 3 - 3, 0) // 10, 23)
    gray = 8 + 10 * step

    def distance(color):
        return sum((a - b) ** 2 for a, b in zip(color, rgb))
    if distance((gray, gray, gray)) < distance(cube):
        return 232 + step
    return 16 + 36 * index[0] + 6 * index[1] + index[2]


def style(*parts):
    """Styles together: SGR parameters, later ones winning."""
    return ";".join(part for part in parts if part)


def between(a, b, t):
    """The colour `t` of the way from `a` to `b`."""
    t = min(max(t, 0.0), 1.0)
    return tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))


class Look:
    """The styles of one colour depth, in the harness's own colours (`pair`, by default the one this
    VM runs): `accent` is its signature, `keys` the screen's sky for the keys under the input box,
    as the pane's own line of keys. Without colour, muted text is dimmed and code is bold."""

    def __init__(self, depth, pair=None):
        self.depth = depth
        self.pair = pair or brand()
        self.accent = self.fg(self.pair[1])
        self.keys = self.fg(SKY)
        self.good = self.fg(GREEN)
        self.warn = self.fg(AMBER)
        self.bad = self.fg(RED)
        self.muted = self.fg(GRAY) if depth else DIM
        self.code = self.fg(PALE) if depth else BOLD

    def fg(self, rgb):
        if self.depth == 24:
            return "38;2;%d;%d;%d" % tuple(rgb)
        if self.depth == 8:
            return "38;5;%d" % nearest_256(rgb)
        return ""

    def mix(self, a, b, t):
        """The colour between two brand colours, for the wordmark's blue-to-sky sweep."""
        return self.fg(between(a, b, t))

    def wave(self, t):
        """The harness's colour at `t` on a loop from its first colour to its signature and back."""
        x = t % 1.0
        return self.mix(self.pair[0], self.pair[1], 1 - abs(2 * x - 1))


def gradient(text, look, extra="", pair=None, light=None, start=0):
    """`text` in the harness's colours from its first to its signature (`pair` to choose others),
    with `extra` (BOLD) on every character; with `light` (a column; the text starts at column
    `start`), a band of white light there. `extra` alone without colour."""
    pair = pair or look.pair
    if not text:
        return []
    if not look.depth:
        return [(text, extra)]
    out, last = [], max(len(text) - 1, 1)
    for i, c in enumerate(text):
        colour = between(pair[0], pair[1], i / last)
        if light is not None:
            colour = between(colour, WHITE, 0.85 * max(0.0, 1.0 - abs(start + i - light) / LIGHT_WIDTH))
        how = style(look.fg(colour), extra)
        if out and out[-1][1] == how:
            out[-1] = (out[-1][0] + c, how)
        else:
            out.append((c, how))
    return out


def light_at(now, length):
    """Where the light running over a line of `length` columns is at `now` (a column, off the line
    between passes): it crosses at LIGHT_SPEED, then pauses LIGHT_PAUSE."""
    run = (length + 2 * LIGHT_WIDTH) / LIGHT_SPEED
    at = now % (run + LIGHT_PAUSE)
    return at * LIGHT_SPEED - LIGHT_WIDTH if at < run else -10 * LIGHT_WIDTH


def shimmer(text, look, now=None, base=SOFT, extra="", start=0, center=None):
    """`text` with the harness's light running over it: a band of its signature colour, brightest in
    the middle, crossing it again and again (each character its own colour). `start`: the column
    the text starts at on a line the light crosses whole; `center` puts the light there instead.
    Plain (with `extra`) without colour."""
    if not text:
        return []
    if not look.depth:
        return [(text, extra)]
    if center is None:
        center = light_at(time.monotonic() if now is None else now, start + width_of(text))
    peak = between(look.pair[1], WHITE, 0.15)
    out, column = [], start
    for c in text:
        t = max(0.0, 1.0 - abs(column - center) / LIGHT_WIDTH)
        how = style(look.fg(between(base, peak, t)), extra)
        if out and out[-1][1] == how:
            out[-1] = (out[-1][0] + c, how)
        else:
            out.append((c, how))
        column += cell_width(c)
    return out


# Lines are lists of (text, style) segments; text holds no line break.

def cell_width(c):
    """Columns a character takes: 2 for wide ones, 0 for controls and marks that combine."""
    code = ord(c)
    if 0x20 <= code < 0x7F:
        return 1
    if code < 0x20 or 0x7F <= code < 0xA0:
        return 0
    if unicodedata.combining(c) or unicodedata.category(c) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1


def width_of(text):
    if text.isascii() and text.isprintable():
        return len(text)
    return sum(cell_width(c) for c in text)


def line_width(line):
    return sum(width_of(text) for text, _ in line)


def plain(line):
    return "".join(text for text, _ in line)


def render(line, colour=True):
    """A line as terminal text: each segment in its style, or plain."""
    out = []
    for text, how in line:
        if text:
            out.append("\x1b[%sm%s\x1b[0m" % (how, text) if colour and how else text)
    return "".join(out)


# Escape sequences, so text from a tool or a model never moves the cursor or changes colours.
ESCAPES = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b[P^_][^\x1b]*(?:\x1b\\)?|\x1b.?")


def clean(text):
    """Text as lines can show it: no escape sequences or control characters, tabs as spaces, every
    line break a "\\n"."""
    text = ESCAPES.sub("", str(text)).replace("\r\n", "\n").replace("\r", "\n")
    return "".join(c for c in text if c in "\n\t" or unicodedata.category(c) != "Cc").expandtabs(4)


def cut(text, room):
    """`text` split where its first part fills `room` columns (at least one character)."""
    used = 0
    for i, c in enumerate(text):
        size = cell_width(c)
        if used + size > room and i > 0:
            return text[:i], text[i:]
        used += size
    return text, ""


def truncate(line, room):
    """A line cut to `room` columns, with … where it was cut."""
    if room <= 0:
        return []
    if line_width(line) <= room:
        return list(line)
    out, used = [], 0
    for text, how in line:
        kept = []
        for c in text:
            size = cell_width(c)
            if used + size > room - 1:
                if kept:
                    out.append(("".join(kept), how))
                out.append(("…", how))
                return out
            kept.append(c)
            used += size
        if kept:
            out.append(("".join(kept), how))
    return out


def wrap(segments, width, first=(), rest=None):
    """The segments' words in lines of at most `width` columns, after `first` on the first line and
    `rest` on the others (by default as many spaces as `first` is wide). Spaces between words stay,
    spaces where a line breaks go, and a word longer than a line is cut."""
    first = list(first)
    rest = [(" " * line_width(first), "")] if rest is None else list(rest)
    if width - line_width(rest) < 8:
        rest = []
    lines, line = [], list(first)
    used = start = line_width(first)
    space = None
    for text, how in segments:
        for piece in re.findall(r" +|[^ ]+", text):
            if piece[0] == " ":
                if used > start:
                    space = (piece, how)
                continue
            size = width_of(piece)
            gap = width_of(space[0]) if space else 0
            if used > start and used + gap + size > width:
                lines.append(line)
                line, space, gap = list(rest), None, 0
                used = start = line_width(rest)
            if space:
                line.append(space)
                used += gap
                space = None
            while used + size > width and size:
                head, piece = cut(piece, max(width - used, 1))
                line.append((head, how))
                lines.append(line)
                line = list(rest)
                used = start = line_width(rest)
                size = width_of(piece)
            if piece:
                line.append((piece, how))
                used += size
    lines.append(line)
    return lines


def wrap_hard(segments, width, first=(), rest=None):
    """The segments as they are, every space kept, cut every `width` columns (code, a tool's
    output). The lines after the first start with `rest` (by default `first`)."""
    first = list(first)
    rest = first if rest is None else list(rest)
    lines, line, used = [], list(first), line_width(first)
    for text, how in segments:
        while text:
            if width - used <= 0:
                lines.append(line)
                line, used = list(rest), line_width(rest)
            head, text = cut(text, max(width - used, 1))
            line.append((head, how))
            used += width_of(head)
            if text:
                lines.append(line)
                line, used = list(rest), line_width(rest)
    lines.append(line)
    return lines


def spread(left, right, width):
    """`left` with `right` at the row's end, as the screen's tool rows; the left is cut to fit."""
    if width >= PLAIN:
        return left + [("  ", "")] + right
    room = width - line_width(right) - 2
    if room < 12:
        return truncate(left, width)
    left = truncate(left, room)
    return left + [(" " * (width - line_width(left) - line_width(right)), "")] + right


def clock(seconds):
    """The CLI's clock: 3.1s, 12s, 1m 05s, 1h 02m."""
    if seconds < 10:
        return "%.1fs" % max(seconds, 0)
    seconds = int(seconds)
    if seconds < 60:
        return "%ds" % seconds
    if seconds < 3600:
        return "%dm %02ds" % (seconds // 60, seconds % 60)
    return "%dh %02dm" % (seconds // 3600, seconds % 3600 // 60)


def size(count):
    """The CLI's byte sizes: 512 B, 12 KB, 1.2 MB."""
    if count < 1024:
        return "%d B" % count
    if count < 1024 * 1024:
        return "%.0f KB" % (count / 1024)
    return "%.1f MB" % (count / 1024 / 1024)


def lines_word(count):
    return "1 more line" if count == 1 else "{:,} more lines".format(count)


# Markdown, as the screen draws a message (harness/src/ui/markdown.rs): headings in bold (the first
# two levels in sky), **bold**, *italic*, `code` in pale blue, code blocks indented, • and 1.
# lists with their lines hanging, quotes after a rail, rules across, tables in columns. A single
# line break stays one: models and tools mean their lines.
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})\s*([^`\s]*)")
HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$")
RULE = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")
ITEM = re.compile(r"^(\s*)([-*+•]|\d{1,3}[.)])\s+(.*)$")
TASK = re.compile(r"^\[([ xX])\]\s+(.*)$")
QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
INLINE = re.compile(
    r"(?P<tick>`+)(?P<code>.+?)(?P=tick)"
    r"|\*\*(?P<strong>[^*\s](?:.*?[^*\s])?)\*\*"
    r"|__(?P<strong2>[^_\s](?:.*?[^_\s])?)__"
    r"|(?<![\w*])\*(?P<em>[^*\s](?:[^*]*?[^*\s])?)\*(?![\w*])"
    r"|(?<![\w_])_(?P<em2>[^_\s](?:[^_]*?[^_\s])?)_(?![\w_])"
    r"|~~(?P<strike>[^~\s](?:.*?[^~\s])?)~~"
    r"|\[(?P<label>[^\]\n]+)\]\((?P<url>[^)\s]+)(?:\s+\"[^\"]*\")?\)"
    r"|<(?P<auto>(?:https?|mailto):[^>\s]+)>"
    r"|\\(?P<escaped>[!-/:-@\[-`{-~])")


def inline(text, how, look):
    """A line's inline Markdown as segments, each over `how`."""
    out, at = [], 0
    for match in INLINE.finditer(text):
        if match.start() > at:
            out.append((text[at:match.start()], how))
        at = match.end()
        part = match.groupdict()
        if part["code"] is not None:
            code = part["code"]
            if len(code) > 2 and code[0] == " " and code[-1] == " " and code.strip():
                code = code[1:-1]
            out.append((code, style(how, look.code)))
        elif part["strong"] or part["strong2"]:
            out += inline(part["strong"] or part["strong2"], style(how, BOLD), look)
        elif part["em"] or part["em2"]:
            out += inline(part["em"] or part["em2"], style(how, ITALIC), look)
        elif part["strike"]:
            out += inline(part["strike"], style(how, STRIKE), look)
        elif part["label"]:
            out += inline(part["label"], style(how, look.accent, UNDERLINE), look)
            if part["label"].strip() != part["url"]:
                out.append((" " + part["url"], style(how, look.muted)))
        elif part["auto"]:
            out.append((part["auto"], style(how, look.accent, UNDERLINE)))
        elif part["escaped"]:
            out.append((part["escaped"], how))
    if at < len(text):
        out.append((text[at:], how))
    return out


def table_rule(line):
    stripped = line.strip()
    return "|" in stripped and "-" in stripped and set(stripped) <= set("|-: ")


def cells(line):
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|") and not stripped.endswith("\\|"):
        stripped = stripped[:-1]
    return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", stripped)]


def table(rows, width, look):
    """A table in columns two spaces apart, its header bold over a rule; one too wide for the
    screen as its rows one after the other, each cell after its column's name."""
    count = max(len(row) for row in rows)
    shown = [[inline(cell, BOLD if k == 0 else "", look) for cell in row + [""] * (count - len(row))]
             for k, row in enumerate(rows)]
    widths = [max(line_width(row[j]) for row in shown) for j in range(count)]
    if sum(widths) + 2 * (count - 1) <= width:
        out = []
        for k, row in enumerate(shown):
            line = []
            for j, cell in enumerate(row):
                line += cell
                if j < count - 1:
                    line.append((" " * (widths[j] - line_width(cell) + 2), ""))
            out.append(line)
            if k == 0:
                out.append([("  ".join("─" * w for w in widths), look.muted)])
        return out
    names = [plain(cell) for cell in shown[0]]
    label = min(max(width_of(name) for name in names) + 2, width // 3)
    out = []
    for k, row in enumerate(shown[1:]):
        if k:
            out.append([])
        for j, cell in enumerate(row):
            if line_width(cell):
                first = [(names[j].ljust(label)[:label], look.muted)]
                out.extend(wrap(cell, width, first, [(" " * label, "")]))
    return out


def markdown(text, width, look):
    """Markdown as lines at most `width` wide."""
    rows = clean(text).split("\n")
    out = []
    hang = None  # where the lines of the last list item go on
    i = 0

    def gap():
        if out and out[-1]:
            out.append([])
    while i < len(rows):
        line = rows[i]
        fence = FENCE.match(line)
        if fence:
            mark, code = fence.group(1), []
            i += 1
            while i < len(rows) and not rows[i].strip().startswith(mark):
                code.append(rows[i])
                i += 1
            i += 1
            margin = min((len(row) - len(row.lstrip(" ")) for row in code if row.strip()), default=0)
            for row in code:
                out.extend(wrap_hard([(row[margin:], look.code)], width, [("  ", "")]))
            hang = None
            continue
        if not line.strip():
            gap()
            i += 1
            continue
        heading = HEADING.match(line)
        if heading:
            if out and out[-1]:
                out.append([])
            level = len(heading.group(1))
            how = style(look.accent, BOLD) if level <= 2 else BOLD
            out.extend(wrap(inline(heading.group(2), how, look), width))
            hang = None
            i += 1
            continue
        if RULE.match(line):
            out.append([("─" * min(width, RULE_WIDTH if width >= PLAIN else width), look.muted)])
            hang = None
            i += 1
            continue
        if line.lstrip().startswith("|") and i + 1 < len(rows) and table_rule(rows[i + 1]):
            found = [cells(line)]
            i += 2
            while i < len(rows) and rows[i].lstrip().startswith("|"):
                found.append(cells(rows[i]))
                i += 1
            out.extend(table(found, width, look))
            hang = None
            continue
        quote = QUOTE.match(line)
        if quote:
            rail = [("│ ", look.muted)]
            out.extend(wrap(inline(quote.group(1), "", look), width, rail, rail))
            i += 1
            continue
        item = ITEM.match(line)
        if item:
            depth = min(len(item.group(1).expandtabs(4)) // 2, 6)
            marker, body = item.group(2), item.group(3)
            mark = (marker + " ", look.muted)
            if marker in "-*+•":
                task = TASK.match(body)
                if task:
                    mark = ("◇ ", look.good) if task.group(1) != " " else ("○ ", look.muted)
                    body = task.group(2)
                else:
                    mark = ("• ", look.muted)
            first = ([("  " * depth, "")] if depth else []) + [mark]
            hang = [(" " * line_width(first), "")]
            out.extend(wrap(inline(body, "", look), width, first, hang))
            i += 1
            continue
        if hang is not None and line[:1] == " ":
            out.extend(wrap(inline(line.strip(), "", look), width, hang, hang))
            i += 1
            continue
        hang = None
        out.extend(wrap(inline(line.strip(), "", look), width))
        i += 1
    while out and not out[-1]:
        out.pop()
    while out and not out[0]:
        out.pop(0)
    return out


def preview(text, width, look, limit=4, indent=6, tail=False):
    """The first lines of a step's output under its row (with `tail`, the last ones), muted, each
    cut to the screen."""
    rows = [row.rstrip() for row in clean(text).split("\n")]
    while rows and not rows[0].strip():
        rows.pop(0)
    while rows and not rows[-1].strip():
        rows.pop()
    pad = [(" " * indent, "")]
    more = len(rows) - limit
    if more > 0 and tail:
        return ([pad + [("… " + lines_word(more).replace("more", "earlier"), look.muted)]]
                + [truncate(pad + [(row, look.muted)], width) for row in rows[-limit:]])
    shown = [truncate(pad + [(row, look.muted)], width) for row in rows[:limit]]
    if more > 0:
        shown.append(pad + [("… " + lines_word(more), look.muted)])
    return shown


# The CLI's wordmark: a 5-pixel font drawn with half blocks (harness/src/ui/draw.rs).
GLYPHS = (
    ("#...#", ".#.#.", "..#..", "..#..", "..#.."),
    (".##.", "#..#", "#..#", "#..#", ".##."),
    ("#..#", "#..#", "#..#", "#..#", ".##."),
    (".###", "#...", "#.##", "#..#", ".###"),
    (".##.", "#..#", "#..#", "#..#", ".##."),
    ("###.", "#..#", "###.", "#.#.", "#..#"),
    ("###", ".#.", ".#.", ".#.", "###"),
)


def wordmark(look, light=None):
    """The YOUGORI wordmark's three rows, in the blue-to-sky sweep; with `light` (a column), a band
    of white light there, as it passes over the wordmark when the banner shows."""
    pixels = [".".join(glyph[row] for glyph in GLYPHS) for row in range(5)]
    columns = len(pixels[0])

    def on(row, x):
        return row < 5 and pixels[row][x] == "#"
    rows = []
    for row in range(3):
        line = []
        for x in range(columns):
            top, bottom = on(row * 2, x), on(row * 2 + 1, x)
            ch = "█" if top and bottom else "▀" if top else "▄" if bottom else " "
            how = ""
            if ch != " ":
                colour = between(BLUE, SKY, x / columns)
                if light is not None:
                    colour = between(colour, WHITE, 0.85 * max(0.0, 1.0 - abs(x - light) / LIGHT_WIDTH))
                how = look.fg(colour)
            if line and line[-1][1] == how:
                line[-1] = (line[-1][0] + ch, how)
            else:
                line.append((ch, how))
        rows.append(line)
    return rows


def hints(keys, width, look):
    """Keys and what they do, as the screen's line of keys: the key in sky, its words muted."""
    line = [(" ", "")]
    for key, words in keys:
        item = [(key, look.keys), (" " + words, look.muted)]
        if line_width(line) + 3 + line_width(item) > width:
            break
        if len(line) > 1:
            line.append(("   ", ""))
        line += item
    return line


# Keys, from what a terminal sends.

def _csi(params, final):
    numbers = params.split(";")
    modifier = int(numbers[1]) if len(numbers) > 1 and numbers[1].isdigit() else 1
    word = modifier in (3, 5, 7)  # with Alt or Ctrl: a word at a time
    if final == "A":
        return ("up",)
    if final == "B":
        return ("down",)
    if final == "C":
        return ("word-right",) if word else ("right",)
    if final == "D":
        return ("word-left",) if word else ("left",)
    if final == "H":
        return ("home",)
    if final == "F":
        return ("end",)
    if final == "~":
        number = numbers[0]
        if number == "200":
            return ("paste-start",)
        if number in ("1", "7"):
            return ("home",)
        if number in ("4", "8"):
            return ("end",)
        if number == "3":
            return ("word-delete",) if word else ("delete",)
    return None


ALT = {"b": ("word-left",), "f": ("word-right",), "d": ("word-delete",), "\x7f": ("word-back",),
       "\x08": ("word-back",), "\r": ("newline",)}
# Backspace is 0x7f from most terminals and 0x08 from the Windows console (the launcher passes its
# keys on as the console gives them), so both delete one character.
CONTROL = {"\r": ("enter",), "\n": ("newline",), "\x7f": ("backspace",), "\x08": ("backspace",),
           "\x01": ("home",), "\x05": ("end",), "\x02": ("left",), "\x06": ("right",), "\x0b": ("kill-end",),
           "\x15": ("kill-start",), "\x17": ("word-back",), "\x03": ("interrupt",), "\x04": ("eof",),
           "\x0c": ("redraw",), "\t": ("tab",)}
PASTE_END = "\x1b[201~"


def _escape(buffer, i):
    """The key of the escape sequence at `i` and its length; length 0 while it is incomplete."""
    if i + 1 >= len(buffer):
        return None, 0
    c = buffer[i + 1]
    if c == "[":
        j = i + 2
        while j < len(buffer) and "0" <= buffer[j] <= "?":
            j += 1
        while j < len(buffer) and " " <= buffer[j] <= "/":
            j += 1
        if j >= len(buffer):
            return None, 0
        return _csi(buffer[i + 2:j], buffer[j]), j + 1 - i
    if c == "O":
        if i + 2 >= len(buffer):
            return None, 0
        return _csi("", buffer[i + 2]), 3
    if c == "\x1b":
        return ("escape",), 1
    return ALT.get(c), 2


class Keys:
    """Turns what the terminal sends into keys: ("text", s), ("paste", s), ("enter",),
    ("backspace",), ("delete",), ("left",) and the others of CONTROL, ALT and _csi. Sequences the
    line has no use for are dropped whole."""

    # How long an unfinished sequence waits for the rest: a lone escape is the Esc key after
    # LONE_ESCAPE; anything else unfinished goes after STALE (its bytes came one by one, slowly).
    LONE_ESCAPE = 0.05
    STALE = 1.0

    def __init__(self):
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.buffer = ""
        self.pasted = None  # a paste under way
        self.since = None  # when the unfinished part began to wait

    def pending(self):
        """How long to wait for more before flush() has something to do; None with nothing waiting."""
        if not self.buffer:
            return None
        return self.LONE_ESCAPE if self.buffer == "\x1b" else 0.1

    def feed(self, data, now=None):
        self.buffer += self.decoder.decode(data)
        keys = self._parse(False)
        if self.buffer and self.since is None:
            self.since = time.monotonic() if now is None else now
        return keys

    def flush(self, now=None):
        """After a pause: an escape left alone is the Esc key; anything else unfinished goes once it
        has waited STALE."""
        now = time.monotonic() if now is None else now
        if self.buffer and self.buffer != "\x1b" and self.since is not None and now - self.since < self.STALE:
            return []
        keys = self._parse(True)
        self.since = None
        return keys

    def _parse(self, final):
        keys, buffer, i = [], self.buffer, 0
        while i < len(buffer):
            if self.pasted is not None:
                end = buffer.find(PASTE_END, i)
                if end < 0:
                    keep = next((n for n in range(len(PASTE_END) - 1, 0, -1) if buffer.endswith(PASTE_END[:n])), 0)
                    if final:
                        keep = 0
                    self.pasted += buffer[i:len(buffer) - keep]
                    i = len(buffer) - keep
                    if final:
                        keys.append(("paste", self.pasted))
                        self.pasted = None
                    break
                keys.append(("paste", self.pasted + buffer[i:end]))
                self.pasted = None
                i = end + len(PASTE_END)
                continue
            c = buffer[i]
            if c == "\x1b":
                key, length = _escape(buffer, i)
                if not length:
                    if final:
                        if buffer[i:] == "\x1b":
                            keys.append(("escape",))
                        i = len(buffer)
                    break
                i += length
                if key == ("paste-start",):
                    self.pasted = ""
                elif key:
                    keys.append(key)
                continue
            if c in CONTROL:
                keys.append(CONTROL[c])
                i += 1
                continue
            j = i
            while j < len(buffer) and buffer[j] not in CONTROL and buffer[j] != "\x1b":
                j += 1
            text = "".join(ch for ch in buffer[i:j] if unicodedata.category(ch) != "Cc")
            if text:
                if keys and keys[-1][0] == "text":
                    keys[-1] = ("text", keys[-1][1] + text)
                else:
                    keys.append(("text", text))
            i = max(j, i + 1)
        self.buffer = buffer[i:]
        if not self.buffer:
            self.since = None
        return keys


def _word_start(text, at):
    while at > 0 and text[at - 1].isspace():
        at -= 1
    while at > 0 and not text[at - 1].isspace():
        at -= 1
    return at


def _word_end(text, at):
    while at < len(text) and text[at].isspace():
        at += 1
    while at < len(text) and not text[at].isspace():
        at += 1
    return at


class Editor:
    """The line being typed, the cursor in it and the earlier lines to go back to."""

    def __init__(self, history=None, complete=()):
        self.text, self.cursor = "", 0
        self.history = [] if history is None else history
        self.recall, self.draft = len(self.history), ""
        self.complete = tuple(complete)

    def insert(self, text):
        text = "".join(c for c in text if c == "\n" or unicodedata.category(c) != "Cc")
        self.text = self.text[:self.cursor] + text + self.text[self.cursor:]
        self.cursor += len(text)

    def line_start(self):
        return self.text.rfind("\n", 0, self.cursor) + 1

    def line_end(self):
        end = self.text.find("\n", self.cursor)
        return len(self.text) if end < 0 else end

    def _recall(self, step):
        """Goes back (-1) or on (+1) through the earlier lines; past the last, to the draft."""
        to = self.recall + step
        if to < 0 or to > len(self.history):
            return
        if self.recall == len(self.history):
            self.draft = self.text
        self.recall = to
        self.text = self.draft if to == len(self.history) else self.history[to]
        self.cursor = len(self.text)

    def _vertical(self, step):
        """Up and down: between the lines of a text of several, else through the history."""
        start = self.line_start()
        if step < 0 and start > 0:
            column = self.cursor - start
            above = self.text.rfind("\n", 0, start - 1) + 1
            self.cursor = min(above + column, start - 1)
        elif step > 0 and self.line_end() < len(self.text):
            column = self.cursor - start
            below = self.line_end() + 1
            end = self.text.find("\n", below)
            end = len(self.text) if end < 0 else end
            self.cursor = min(below + column, end)
        else:
            self._recall(step)

    def _complete(self):
        """Tab: the one command that starts with what is typed, or what all that do share."""
        typed = self.text[:self.cursor]
        if not typed or "\n" in typed or " " in typed:
            return
        found = [word for word in self.complete if word.startswith(typed)]
        if not found:
            return
        shared = os.path.commonprefix(found)
        if len(found) == 1:
            shared += " "
        if len(shared) > len(typed):
            self.insert(shared[len(typed):])

    def key(self, key):
        """Takes a key; says "submit", "interrupt", "eof" or "redraw" when the line has more to do
        than draw itself again."""
        kind = key[0]
        text, at = self.text, self.cursor
        if kind == "text":
            self.insert(key[1])
        elif kind == "paste":
            self.insert(ESCAPES.sub("", key[1]).replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    "))
        elif kind == "enter":
            return "submit"
        elif kind == "newline":
            self.insert("\n")
        elif kind == "backspace":
            if at:
                self.text, self.cursor = text[:at - 1] + text[at:], at - 1
        elif kind == "delete":
            self.text = text[:at] + text[at + 1:]
        elif kind == "word-back":
            start = _word_start(text, at)
            self.text, self.cursor = text[:start] + text[at:], start
        elif kind == "word-delete":
            self.text = text[:at] + text[_word_end(text, at):]
        elif kind == "left":
            self.cursor = max(at - 1, 0)
        elif kind == "right":
            self.cursor = min(at + 1, len(text))
        elif kind == "word-left":
            self.cursor = _word_start(text, at)
        elif kind == "word-right":
            self.cursor = _word_end(text, at)
        elif kind == "home":
            self.cursor = self.line_start()
        elif kind == "end":
            self.cursor = self.line_end()
        elif kind == "kill-start":
            start = self.line_start()
            self.text, self.cursor = text[:start] + text[at:], start
        elif kind == "kill-end":
            self.text = text[:at] + text[self.line_end():]
        elif kind == "up":
            self._vertical(-1)
        elif kind == "down":
            self._vertical(1)
        elif kind == "tab":
            self._complete()
        elif kind == "escape":
            self.text, self.cursor = "", 0
        elif kind == "interrupt":
            return "interrupt"
        elif kind == "eof":
            if not text:
                return "eof"
            self.text = text[:at] + text[at + 1:]
        elif kind == "redraw":
            return "redraw"
        return None


def layout(text, cursor, room):
    """The text's rows at `room` columns, as (start, end) in the text, and the cursor's row and
    column. Each line of the text starts a row; a line wider than the room goes on in the next."""
    rows, at = [], 0
    for part in text.split("\n"):
        begin, end = at, at + len(part)
        start, used = begin, 0
        for k in range(begin, end):
            size = cell_width(text[k])
            if used + size > room and k > start:
                rows.append((start, k))
                start, used = k, 0
            used += size
        rows.append((start, end))
        at = end + 1
    for r, (start, end) in enumerate(rows):
        if start <= cursor <= end:
            if cursor == end and r + 1 < len(rows) and rows[r + 1][0] == end:
                continue  # the end of a row that goes on: the start of the next
            column = width_of(text[start:cursor])
            if column >= room:
                rows.insert(r + 1, (end, end))
                return rows, r + 1, 0
            return rows, r, column
    last = rows[-1]
    return rows, len(rows) - 1, width_of(text[last[0]:last[1]])


def line_word(empty):
    """What the input box tells the Yougori screen's pane of its line (OSC 7717, which the pane
    draws nothing for): whether it is `empty`. Only inside that pane (YOUGORI_PANE=1)."""
    if os.environ.get("YOUGORI_PANE") != "1":
        return ""
    return "\x1b]7717;line;%s\x07" % ("empty" if empty else "text")


class HangUp(KeyboardInterrupt):
    """The terminal hung up: the Yougori screen stops the harness (/new, another model, quit) or
    the session ends. The harness ends as Ctrl+C at its prompt ends it, so its servers stop too."""


def edge(text, look, phase, direction=1):
    """A border row of the input box in the harness's colours, one loop of them along it, flowing
    (`direction` 1 to the right, -1 to the left) as `phase` goes from 0 to 1; muted without colour."""
    if not look.depth:
        return [(text, look.muted)]
    out, size = [], max(len(text), 1)
    for at in range(0, len(text), FLOW_STEP):
        out.append((text[at:at + FLOW_STEP], look.wave(direction * at / size - phase)))
    return out


class Box:
    """The input box at the bottom of the output: a rounded border in the harness's colours, which
    flow round it while it waits, `›` and the line (or what to type, muted), and the keys under it.
    It is drawn again after each key, in place, and while it waits as its colours move."""

    def __init__(self, screen, editor, placeholder, keys, look):
        self.screen, self.editor, self.placeholder, self.keys, self.look = screen, editor, placeholder, keys, look
        self.offset = 0  # rows from the box's top to the cursor
        self.said = None  # what the pane was last told of the line: empty (True) or not

    def lines(self, width, height, now=None):
        look, editor = self.look, self.editor
        room = max(width - 6, 4)
        rows, row, column = layout(editor.text, editor.cursor, room)
        most = max(1, min(10, height - 4))
        top = min(max(row - most + 1, 0), max(len(rows) - most, 0))
        # The top runs right and the bottom left, so the colours go round; the corners and the sides
        # meet them in one colour.
        phase = ((time.monotonic() if now is None else now) / FLOW_SECONDS) % 1.0
        side = look.wave(-phase) if look.depth else look.muted
        out = [edge("╭" + "─" * (width - 2) + "╮", look, phase)]
        for r, (start, end) in enumerate(rows[top:top + most]):
            lead = ("› ", look.accent) if top + r == 0 else ("  ", "")
            if not editor.text:
                body = [(self.placeholder, look.muted)] if r == 0 else []
            else:
                body = [(editor.text[start:end], "")]
            body = truncate(body, room)
            fill = width - 5 - line_width(body)
            out.append([("│", side), (" ", ""), lead] + body + [(" " * max(fill, 0), ""), ("│", side)])
        out.append(edge("╰" + "─" * (width - 2) + "╯", look, phase, -1))
        out.append(hints(self.keys, width - 1, look))
        return out, 1 + row - top, 4 + (column if editor.text else 0)

    def draw(self):
        width, height = self.screen.width(), self.screen.height()
        lines, row, column = self.lines(width, height)
        text = ("\x1b[%dA" % self.offset if self.offset else "") + "\r\x1b[J"
        text += "\r\n".join(render(line) for line in lines)
        up = len(lines) - 1 - row
        text += ("\x1b[%dA" % up if up else "") + "\r" + ("\x1b[%dC" % column if column else "") + "\x1b[?25h"
        self.screen.write(text + self.word(not self.editor.text))
        self.offset = row

    def erase(self):
        self.screen.write(("\x1b[%dA" % self.offset if self.offset else "") + "\r\x1b[J" + self.word(True))
        self.offset = 0

    def word(self, empty):
        """The line's word for the pane when it changed (a redraw for the colours alone says nothing,
        so a word from before cannot arrive after keys the pane has sent since)."""
        if self.said == empty:
            return ""
        self.said = empty
        return line_word(empty)


class Work:
    """What the live row says while work runs: `label`, and `detail` after it, muted."""

    def __init__(self, label):
        self.label, self.detail, self.started = label, "", time.monotonic()


class Step:
    """A step on a row of its own: its kind and title; once it has run, `note` after the title and
    `output`'s first lines under the row. fail() marks it failed, with why."""

    def __init__(self, kind, title):
        self.kind, self.title = kind, title
        self.note, self.output, self.failure, self.started = "", "", None, time.monotonic()

    def fail(self, why=""):
        self.failure = why or ""


class Screen:
    """The terminal the harness's lines go to and the person's lines come from. Lines are written
    above the live row (the spinner of the work under way), so it always stays last."""

    def __init__(self):
        self.lock = threading.RLock()
        self.live = None
        self.live_shown = False
        self.blank = True  # whether the last line written was empty
        self.epoch = time.monotonic()
        self.histories = {}
        # Set by a hangup: an input box read on another thread (HexStrike's) ends too.
        self.hung_up = threading.Event()
        # The harness the banner named, whose colours the screen takes (SOLVER_HARNESS's before).
        self.harness = None

    # Where lines go and keys come from, looked up each time (the tests redirect sys.stdout).
    @staticmethod
    def stream():
        return sys.stdout

    def terminal(self):
        try:
            return termios is not None and self.stream().isatty() and sys.stdin.isatty()
        except (AttributeError, ValueError, OSError):
            return False

    def look(self):
        return Look(color_depth() if self.terminal() else 0, brand(self.harness))

    def _size(self):
        try:
            return os.get_terminal_size(self.stream().fileno())
        except (AttributeError, ValueError, OSError):
            return None

    def width(self):
        if not self.terminal():
            return PLAIN
        found = self._size()
        return max(found.columns if found and found.columns else 100, 20)

    def height(self):
        found = self._size()
        return max(found.lines if found and found.lines else 30, 6)

    def write(self, text):
        with self.lock:
            stream = self.stream()
            try:
                stream.write(text)
                stream.flush()
            except (OSError, ValueError):
                pass

    def spinner(self):
        return SPINNER[int((time.monotonic() - self.epoch) / SPIN_SECONDS) % len(SPINNER)]

    # Output.

    def lines(self, lines):
        """Writes whole lines (above the live row while one turns)."""
        lines = list(lines)
        if not lines:
            return
        with self.lock:
            if self.terminal():
                text = "".join(render(line) + "\n" for line in lines)
                if self.live_shown:
                    text = "\r\x1b[K" + text
                    self.live_shown = False
                self.write(text)
                if self.live is not None:
                    self._draw_live()
            else:
                self.write("".join(render(line, colour=False).rstrip() + "\n" for line in lines))
            self.blank = not lines[-1] or not plain(lines[-1]).strip()

    def gap(self):
        """One empty line, unless the last line was one."""
        if not self.blank:
            self.lines([[]])

    def banner(self, name, model, facts=(), tagline=""):
        """The screen's banner: the YOUGORI wordmark with the harness (in its colours) and the model
        beside it, then `facts` (label, value) under it. As it shows, a light passes once over the
        wordmark and the harness's name."""
        self.harness = name
        look, width = self.look(), self.width()
        tag = [("  " + tagline, look.muted)] if tagline else []
        model_line = [(model, look.muted)]
        mark_width = line_width(wordmark(look)[0])
        # Where the harness's name starts beside the wordmark: the light goes on over it.
        name_at = 2 + mark_width + 4
        beside = width >= name_at + max(width_of(name) + line_width(tag), width_of(model))

        def mark_rows(light=None):
            rows = []
            for row, segments in enumerate(wordmark(look, None if light is None else light - 2)):
                line = [("  ", "")] + segments
                if beside and row == 1:
                    line += [("    ", "")] + gradient(name, look, BOLD, light=light, start=name_at) + tag
                elif beside and row == 2:
                    line += [("    ", "")] + model_line
                rows.append(truncate(line, width))
            return rows
        lines = [[]]
        if width < mark_width + 4:
            lines.append(truncate([("  ", ""), ("YOUGORI", style(look.fg(SKY), BOLD)), ("  ", "")]
                                  + gradient(name, look, BOLD) + tag, width))
            lines.append(truncate([("  ", "")] + model_line, width))
        else:
            lines += mark_rows()
            if not beside:
                lines += [truncate([("  ", "")] + gradient(name, look, BOLD) + tag, width),
                          truncate([("  ", "")] + model_line, width)]
        facts = [(label, value) for label, value in facts if value]
        if facts:
            lines.append([])
            label = max(width_of(label) for label, _ in facts) + 2
            for name_, value in facts:
                first = [("  ", ""), (name_.ljust(label), look.muted)]
                for k, part in enumerate(clean(value).split("\n")):
                    lines += wrap([(part, "")], width, first if k == 0 else [(" " * (2 + label), "")],
                                  [(" " * (2 + label), "")])
        lines.append([])
        self.lines(lines)
        if width >= mark_width + 4 and look.depth and self.terminal() and len(lines) < self.height():
            self._sweep(mark_rows, len(lines), (name_at + width_of(name)) if beside else 2 + mark_width)

    def _sweep(self, rows, written, end):
        """A light passing once over the wordmark's rows (`rows(light)`, the first of `written`
        lines just written, after an empty one) up to column `end`, then the rows as they stay."""
        light, frames = -LIGHT_WIDTH, []
        while light <= end + LIGHT_WIDTH:
            frames.append(light)
            light += SWEEP_STEP
        for light in frames + [None]:
            text = "".join("\x1b7\x1b[%dA\r%s\x1b[K\x1b8" % (written - 1 - k, render(line))
                           for k, line in enumerate(rows(light)))
            self.write(text)
            if light is not None:
                time.sleep(SWEEP_FRAME)

    def user(self, text):
        """The person's turn, after `›`, as the screen shows it."""
        look, width = self.look(), self.width()
        lines = []
        for k, part in enumerate(clean(text).strip("\n").split("\n")):
            first = [("› ", look.accent)] if k == 0 else [("  ", "")]
            lines += wrap([(part, "")], width, first, [("  ", "")])
        self.gap()
        self.lines(lines + [[]])

    def markdown(self, text, indent=2):
        """A message in Markdown, indented as the screen's messages."""
        look, width = self.look(), self.width()
        pad = [(" " * indent, "")]
        rendered = markdown(text, max(width - indent, 8), look)
        if rendered:
            self.gap()
            self.lines([pad + line if line else [] for line in rendered])
            self.lines([[]])

    def say(self, text, how="muted", mark=None):
        """A line of the harness's own, indented: muted by default, or in `how` (good, warn, bad,
        or "" for plain), after `mark` when given (▲ for a warning, ■ for an error) in that colour.
        After a good mark (◇) the words stay plain."""
        look, width = self.look(), self.width()
        tone = getattr(look, how, how) if how and not (mark and how == "good") else ""
        first = [("  ", "")] + ([(mark + " ", getattr(look, how, ""))] if mark else [])
        lines = []
        for k, part in enumerate(clean(text).strip("\n").split("\n")):
            lines += wrap([(part, tone)], width, first if k == 0 else [(" " * line_width(first), "")],
                          [(" " * line_width(first), "")])
        self.lines(lines)

    def note(self, text):
        self.say(text)

    def warn(self, text):
        self.say(text, "warn", "▲")

    def error(self, text):
        self.gap()
        look = self.look()
        width = self.width()
        lines = []
        for k, part in enumerate(clean(text).strip("\n").split("\n")):
            first = [("  ", ""), ("■ ", look.bad)] if k == 0 else [("    ", "")]
            lines += wrap([(part, "")], width, first, [("    ", "")])
        self.lines(lines)

    def section(self, title, how="accent", mark="◆"):
        """A heading of the harness's own: `mark` in its colour, then the title in bold."""
        look = self.look()
        self.gap()
        self.lines([truncate([("  ", ""), (mark + " ", getattr(look, how, "")), (title, BOLD)], self.width())])

    def output(self, text, limit=4, tail=False):
        """The first lines of a step's output, under its row (with `tail`, its last lines)."""
        self.lines(preview(text, self.width(), self.look(), limit, tail=tail))

    def pairs(self, pairs, key="accent"):
        """Names and what they mean in two columns (commands, settings), the names in `key`'s
        colour, the meanings wrapped under themselves."""
        look, width = self.look(), self.width()
        pairs = [(name, meaning) for name, meaning in pairs if name]
        if not pairs:
            return
        column = min(max(width_of(name) for name, _ in pairs) + 3, max(width // 3, 8))
        lines = []
        for name, meaning in pairs:
            first = [("  ", ""), (name.ljust(column - 1)[:column - 1] + " ", getattr(look, key, key))]
            lines += wrap([(meaning, "")], width, first, [(" " * (2 + column), "")])
        self.lines(lines)

    def row(self, kind, title, state="done", note="", seconds=None):
        """A step's row once it ran ("done", "failed") or while its result is awaited ("waiting")."""
        look = self.look()
        mark = {"done": ("◇ ", look.good), "failed": ("■ ", look.bad), "waiting": ("○ ", look.muted)}[state]
        left = [("  ", ""), mark, (kind.ljust(KIND), BOLD), (clean(title).replace("\n", " "), "")]
        if note:
            left.append(("  " + note, look.muted))
        right = [(state, look.bad if state == "failed" else look.muted)]
        if seconds is not None:
            right.append((" " + clock(seconds), look.muted))
        self.lines([spread(left, right, self.width())])

    def thought(self, text, limit=3):
        """Thinking, folded as the screen folds it: ▸ Thinking · its words, then its first lines
        after a rail."""
        look, width = self.look(), self.width()
        italic = style(look.muted, ITALIC)
        words = len(text.split())
        count = "1 word" if words == 1 else "{:,} words".format(words)
        rail = [("  ", ""), ("│ ", look.muted)]
        body = []
        for part in clean(text).strip().split("\n"):
            if part.strip():
                body += wrap([(part.strip(), italic)], width, rail, rail)
        lines = [[("  ", ""), ("▸ ", look.muted), ("Thinking · " + count, italic)]] + body[:limit]
        if len(body) > limit:
            lines.append(rail + [("… " + lines_word(len(body) - limit), look.muted)])
        self.gap()
        self.lines(lines)

    # The live row.

    def _draw_live(self):
        row = truncate(self.live(), self.width() - 1)
        self.write("\r\x1b[K" + render(row) + "\r")
        self.live_shown = True

    @contextlib.contextmanager
    def _turning(self, row):
        """`row()` as the live row, turning, while the block runs."""
        if not self.terminal():
            yield
            return
        stop = threading.Event()
        with self.lock:
            before = self.live
            self.live = row
            self.write("\x1b[?25l")
            self._draw_live()

        def turn():
            while not stop.wait(0.1):
                with self.lock:
                    if self.live is row:
                        self._draw_live()
        thread = threading.Thread(target=turn, name="yougori-live-row", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(1)
            with self.lock:
                self.live = before
                if self.live_shown:
                    self.write("\r\x1b[K")
                    self.live_shown = False
                if before is not None:
                    self._draw_live()

    @contextlib.contextmanager
    def working(self, label, hint="ctrl+c to stop"):
        """The live row while the block runs: the spinner turning through the harness's colours,
        `label` with its light running over it, how long so far, and `hint`."""
        work = Work(label)
        look = self.look()

        def row():
            now = time.monotonic()
            line = [("  ", ""), (self.spinner() + " ", look.wave(now / SPIN_COLOUR_SECONDS))]
            # The label in the harness's colours, a white light running over it.
            line += gradient(work.label, look, BOLD, light=light_at(now, width_of(work.label)))
            if work.detail:
                line.append(("  " + work.detail, look.muted))
            line.append(("  " + clock(time.monotonic() - work.started), look.muted))
            if hint:
                line.append((" · " + hint, look.muted))
            return line
        with self._turning(row):
            yield work

    @contextlib.contextmanager
    def step(self, kind, title, hint=""):
        """A step's row: turning while the block runs (the light running over its title), then
        done, failed (step.fail(), or an error out of the block) or stopped (Ctrl+C), with its time,
        note and output. Its kind is in the harness's colour."""
        step = Step(kind, title)
        look = self.look()
        kind_style = style(look.accent, BOLD)

        def row():
            now = time.monotonic()
            left = [("  ", ""), (self.spinner() + " ", look.wave(now / SPIN_COLOUR_SECONDS)),
                    (kind.ljust(KIND), kind_style)] + shimmer(clean(title).replace("\n", " "), look, now)
            right = [("running " + clock(time.monotonic() - step.started), look.muted)]
            if hint:
                right.append((" · " + hint, look.muted))
            return spread(left, right, self.width() - 1)
        stopped = False
        try:
            with self._turning(row):
                yield step
        except BaseException as error:
            stopped = isinstance(error, KeyboardInterrupt) or type(error).__name__ == "CancelledError"
            if step.failure is None and not stopped:
                step.failure = clean(str(error)).strip() or type(error).__name__
            raise
        finally:
            seconds = time.monotonic() - step.started
            if stopped:
                mark, word, tone = ("■ ", look.muted), "stopped", look.muted
            elif step.failure is not None:
                mark, word, tone = ("■ ", look.bad), "failed", look.bad
            else:
                mark, word, tone = ("◇ ", look.good), "done", look.muted
            left = [("  ", ""), mark, (kind.ljust(KIND), kind_style), (step.title, "")]
            if step.note:
                left.append(("  " + step.note, look.muted))
            lines = [spread(left, [(word, tone), (" " + clock(seconds), look.muted)], self.width())]
            if step.failure:
                lines += wrap([(step.failure, look.bad)], self.width(), [(" " * 6, "")])
            if step.output:
                lines += preview(step.output, self.width(), look)
            self.lines(lines)

    # Input.

    @contextlib.contextmanager
    def session(self):
        """While the harness runs: keys typed between its questions do not show up on its screen
        (Ctrl+C still stops what runs), and the cursor shows only in the input box."""
        if not self.terminal():
            yield
            return
        fd = sys.stdin.fileno()
        try:
            saved = termios.tcgetattr(fd)
            quiet = termios.tcgetattr(fd)
            quiet[3] &= ~termios.ECHO
            termios.tcsetattr(fd, termios.TCSANOW, quiet)
        except termios.error:
            saved = None
        restore = self.hang_up_cleanly()
        try:
            yield
        finally:
            restore()
            with self.lock:
                if self.live_shown:
                    self.write("\r\x1b[K")
                    self.live_shown = False
                self.write("\x1b[?25h")
            if saved is not None:
                with contextlib.suppress(termios.error):
                    termios.tcsetattr(fd, termios.TCSADRAIN, saved)

    def hang_up_cleanly(self):
        """Until the call it returns: a hangup or SIGTERM raises HangUp where the harness is (and
        ends an input box read on another thread), once, so its own cleanup then runs to the end,
        unless the harness handles them itself (Shannon does)."""
        taken = []

        def hang_up(number, frame):
            if self.hung_up.is_set():
                return  # already ending: its cleanup goes on
            self.hung_up.set()
            raise HangUp()
        for name in ("SIGHUP", "SIGTERM"):
            number = getattr(signal, name, None)
            try:
                if number is not None and signal.getsignal(number) == signal.SIG_DFL:
                    signal.signal(number, hang_up)
                    taken.append(number)
            except (ValueError, OSError):  # not the main thread: left as they are
                pass

        def restore():
            for number in taken:
                with contextlib.suppress(ValueError, OSError):
                    if signal.getsignal(number) is hang_up:
                        signal.signal(number, signal.SIG_DFL)
        return restore

    @contextlib.contextmanager
    def suspended(self):
        """The terminal as the harness found it, for a program that reads it itself (a shell)."""
        if not self.terminal():
            yield
            return
        fd = sys.stdin.fileno()
        now = termios.tcgetattr(fd)
        cooked = termios.tcgetattr(fd)
        cooked[3] |= termios.ECHO | termios.ICANON | termios.ISIG
        termios.tcsetattr(fd, termios.TCSADRAIN, cooked)
        self.write("\x1b[?25h")
        try:
            yield
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, now)
            self.blank = False

    def ask(self, placeholder, keys=(("enter", "send"),), complete=(), plain="> ", history="line"):
        """The person's next line, typed in the input box (`placeholder` until they type). Up and
        down go back through the lines sent before, Tab completes `complete`'s commands. What they
        send then shows as their turn. Ctrl+C raises KeyboardInterrupt, Ctrl+D on an empty line
        EOFError. Without a terminal: input(plain)."""
        if not self.terminal():
            return input(plain)
        look = self.look()
        past = self.histories.setdefault(history, [])
        editor = Editor(past, complete)
        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
        raw = termios.tcgetattr(fd)
        raw[0] &= ~(termios.IXON | termios.ICRNL | termios.INLCR | termios.IGNCR)
        raw[3] &= ~(termios.ICANON | termios.ECHO | termios.ISIG | termios.IEXTEN)
        raw[6][termios.VMIN], raw[6][termios.VTIME] = 1, 0
        # Keys typed while the work ran are not taken as the answer.
        termios.tcsetattr(fd, termios.TCSAFLUSH, raw)
        box = Box(self, editor, placeholder, keys, look)
        parser = Keys()
        self.gap()
        self.write("\x1b[?2004h")
        # With colour the box's border flows while it waits: it is drawn again every FLOW_TICK.
        tick = FLOW_TICK if look.depth else 0.25
        try:
            box.draw()
            drawn = time.monotonic()
            seen = (self.width(), self.height())
            while True:
                if self.hung_up.is_set():
                    box.erase()
                    raise HangUp()
                wait = parser.pending()
                ready, _, _ = select.select([fd], [], [], tick if wait is None else min(wait, tick))
                if ready:
                    data = os.read(fd, 4096)
                    if not data:
                        box.erase()
                        raise EOFError
                    found = parser.feed(data)
                else:
                    found = parser.flush()
                    if (self.width(), self.height()) != seen:
                        seen = (self.width(), self.height())
                        box.draw()
                        drawn = time.monotonic()
                    elif look.depth and time.monotonic() - drawn >= FLOW_TICK:
                        box.draw()
                        drawn = time.monotonic()
                for key in found:
                    outcome = editor.key(key)
                    if outcome == "submit":
                        box.erase()
                        text = editor.text
                        if text.strip() and (not past or past[-1] != text):
                            past.append(text)
                        if text.strip():
                            self.blank = True
                            self.user(text)
                        return text
                    if outcome == "interrupt":
                        box.erase()
                        raise KeyboardInterrupt
                    if outcome == "eof":
                        box.erase()
                        raise EOFError
                    if outcome == "redraw":
                        self.write("\x1b[2J\x1b[H")
                        box.offset = 0
                if found:
                    box.draw()
                    drawn = time.monotonic()
        finally:
            self.write("\x1b[?2004l\x1b[?25l")
            with contextlib.suppress(termios.error):
                termios.tcsetattr(fd, termios.TCSADRAIN, saved)


screen = Screen()


def brief(text, argv):
    """Shows `text` as the line to paste at the program's first question (a light passing once over
    its heading), then runs it."""
    look = screen.look()
    lines = clean(text).strip().split("\n")
    head, rest = lines[0], [line for line in lines[1:] if line.strip()]

    def heading(light=None):
        words = [(head, BOLD)] if light is None else shimmer(head, look, base=TEXT, extra=BOLD, start=4, center=light)
        return [("  ", ""), ("◆ ", look.accent)] + words
    # The line to paste stays one line however long, so it is copied whole.
    shown = [[], heading()] + [[("  ", ""), (line, look.accent)] for line in rest] + [[]]
    screen.lines(shown)
    if look.depth and screen.terminal() and len(shown) < screen.height():
        screen._sweep(lambda light: [heading(light)], len(shown), 4 + width_of(head))
    sys.stdout.flush()
    os.execvp(argv[0], argv)


if __name__ == "__main__":
    if len(sys.argv) >= 5 and sys.argv[1] == "brief" and sys.argv[3] == "--":
        brief(sys.argv[2], sys.argv[4:])
    sys.exit("Use: terminal_ui.py brief TEXT -- PROGRAM ARGS...")
