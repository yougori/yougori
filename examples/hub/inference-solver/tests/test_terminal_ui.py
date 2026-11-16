"""terminal_ui.py: the look the security harnesses' own terminals draw in (the Yougori screen's
colours, Markdown, step rows, the input box) and the line the person types in it, which edits as in
any terminal program and never shows a key it has no use for as a code (Backspace from the Windows
console arrived as ^H and showed up as ^H)."""
import contextlib
import io
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "site/harnesses"))
import terminal_ui
from terminal_ui import Editor, Keys, Look, layout, markdown, plain, wrap

LOOK = Look(24)
BARE = Look(0)


def texts(lines):
    return [plain(line) for line in lines]


def typed(*chunks, editor=None):
    """The editor after the terminal sent `chunks` (bytes), and what it said last."""
    editor = editor or Editor()
    keys, said = Keys(), None
    for chunk in chunks:
        for key in keys.feed(chunk):
            said = editor.key(key) or said
    for key in keys.flush():
        said = editor.key(key) or said
    return editor, said


class Keystrokes(unittest.TestCase):
    def test_backspace_deletes_one_character_from_any_terminal(self):
        # 0x7f from xterm, Windows Terminal and the pane; 0x08 from the Windows console.
        for backspace in (b"\x7f", b"\x08"):
            with self.subTest(backspace=backspace):
                editor, _ = typed(b"scan", backspace)
                self.assertEqual((editor.text, editor.cursor), ("sca", 3))

    def test_delete_and_the_arrows_edit_instead_of_showing_codes(self):
        editor, _ = typed(b"hello", b"\x1b[D\x1b[D", b"\x1b[3~", b"X")
        self.assertEqual(editor.text, "helXo")
        self.assertNotIn("[", editor.text)
        # Home, End and the words, in each way terminals send them.
        editor, _ = typed(b"one two three", b"\x1b[H", b">", b"\x1bOF", b"<", b"\x1b[1;5D", b"|")
        self.assertEqual(editor.text, ">one two |three<")
        editor, _ = typed(b"a b", b"\x01", b"[", b"\x05", b"]", b"\x1b[1~", b"(", b"\x1b[4~", b")")
        self.assertEqual(editor.text, "([a b])")

    def test_keys_it_has_no_use_for_are_dropped_whole(self):
        editor, _ = typed(b"ok", b"\x1b[15~", b"\x1b[1;2P", b"\x1b[200;1x", b"\x1bOP", b"\x1b[Z", b"!")
        self.assertEqual(editor.text, "ok!")

    def test_sequences_split_across_reads_still_make_one_key(self):
        editor, _ = typed(b"abc", b"\x1b", b"[", b"D", b"\x1b[", b"3", b"~")
        self.assertEqual(editor.text, "ab")
        editor, _ = typed("café ".encode()[:4], "café ".encode()[4:], "😀".encode())
        self.assertEqual(editor.text, "café 😀")

    def test_a_sequence_that_comes_slowly_still_makes_its_key(self):
        keys, editor = Keys(), Editor()
        for key in keys.feed(b"abc\x1b[D\x1b[3", now=10.0):
            editor.key(key)
        self.assertEqual(keys.flush(now=10.5), [], "an unfinished sequence waits for the rest")
        for key in keys.feed(b"~", now=10.6):
            editor.key(key)
        self.assertEqual(editor.text, "ab")
        keys.feed(b"\x1b[3", now=20.0)
        self.assertEqual(keys.flush(now=21.5), [], "after a long wait it goes, whole")
        self.assertIsNone(keys.pending())
        keys.feed(b"\x1b", now=30.0)
        self.assertEqual(keys.flush(now=30.06), [("escape",)], "a lone escape is the Esc key at once")

    def test_a_lone_escape_clears_the_line_and_a_paste_is_taken_whole(self):
        editor, _ = typed(b"draft", b"\x1b")
        self.assertEqual(editor.text, "")
        editor, _ = typed(b"\x1b[200~line one\r\nline \x1b[D two\x1b[201~")
        self.assertEqual(editor.text, "line one\nline  two")
        editor, _ = typed(b"\x1b[200~split ", b"paste\x1b[2", b"01~!")
        self.assertEqual(editor.text, "split paste!")

    def test_words_lines_and_what_the_editor_says(self):
        editor, said = typed(b"scan http://x \x17")
        self.assertEqual((editor.text, said), ("scan ", None))
        editor, said = typed(b"one two\x1b\x7f")
        self.assertEqual(editor.text, "one ")
        editor, _ = typed(b"keep this\x1b[1;5D\x0b")
        self.assertEqual(editor.text, "keep ")
        editor, _ = typed(b"gone\x15kept")
        self.assertEqual(editor.text, "kept")
        self.assertEqual(typed(b"go\r")[1], "submit")
        self.assertEqual(typed(b"go\x03")[1], "interrupt")
        self.assertEqual(typed(b"\x04")[1], "eof")
        editor, said = typed(b"x\x04")
        self.assertEqual((editor.text, said), ("x", None), "Ctrl+D deletes when the line has text")
        editor, _ = typed(b"first\x1b\rsecond")
        self.assertEqual(editor.text, "first\nsecond", "Shift+Enter (sent as Alt+Enter) starts a new line")

    def test_up_and_down_go_through_earlier_lines_and_lines_of_the_text(self):
        history = ["scan http://a", "check"]
        editor, _ = typed(b"dra", editor=Editor(history))
        typed(b"\x1b[A", editor=editor)
        self.assertEqual(editor.text, "check")
        typed(b"\x1b[A\x1b[A", editor=editor)
        self.assertEqual(editor.text, "scan http://a")
        typed(b"\x1b[B\x1b[B", editor=editor)
        self.assertEqual(editor.text, "dra", "past the last line: the draft again")
        editor, _ = typed(b"ab\x1b\rcdef\x1b[A", editor=Editor(["old"]))
        self.assertEqual((editor.text, editor.cursor), ("ab\ncdef", 2), "up within the text before the history")

    def test_tab_completes_a_command(self):
        editor, _ = typed(b"sc\t", editor=Editor(complete=("scan", "check", "shell")))
        self.assertEqual(editor.text, "scan ")
        editor, _ = typed(b"s\t", editor=Editor(complete=("scan", "shell")))
        self.assertEqual(editor.text, "s", "two commands share only what is typed")
        editor, _ = typed(b"x\t", editor=Editor(complete=("scan",)))
        self.assertEqual(editor.text, "x")


class Layout(unittest.TestCase):
    def test_rows_wrap_at_the_room_and_the_cursor_follows(self):
        rows, row, column = layout("abcdefgh", 8, 4)
        self.assertEqual((rows, row, column), ([(0, 4), (4, 8), (8, 8)], 2, 0))
        rows, row, column = layout("abcdef", 4, 4)
        self.assertEqual((row, column), (1, 0), "the end of a full row is the start of the next")
        rows, row, column = layout("ab\ncd", 3, 10)
        self.assertEqual((rows, row, column), ([(0, 2), (3, 5)], 1, 0))
        self.assertEqual(layout("", 0, 10), ([(0, 0)], 0, 0))
        # Wide characters take two columns.
        rows, _, column = layout("日本語", 3, 4)
        self.assertEqual((rows, column), ([(0, 2), (2, 3)], 2))

    def test_the_box_draws_the_line_inside_a_rounded_border_with_the_keys(self):
        box = terminal_ui.Box(terminal_ui.Screen(), Editor(), "Tell HexStrike what to test next",
                              (("enter", "send"), ("/exit", "leave")), LOOK)
        lines, row, column = box.lines(40, 20)
        shown = texts(lines)
        self.assertEqual(shown[0], "╭" + "─" * 38 + "╮")
        self.assertEqual(shown[1], "│ › Tell HexStrike what to test next   │")
        self.assertEqual(shown[2], "╰" + "─" * 38 + "╯")
        self.assertEqual(shown[3], " enter send   /exit leave")
        self.assertEqual((row, column), (1, 4))
        self.assertTrue(all(len(line) == 40 for line in shown[:3]))
        box.editor.insert("x" * 50)
        lines, row, column = box.lines(40, 20)
        self.assertEqual(texts(lines)[2], "│   " + "x" * 16 + " " * 19 + "│")
        self.assertEqual((row, column), (2, 4 + 16))


class Colours(unittest.TestCase):
    """Each security harness in its own colours, with light that moves over what runs."""

    def test_each_security_harness_has_its_own_colours(self):
        self.assertEqual(terminal_ui.brand("HexStrike"), terminal_ui.BRANDS["hexstrike"])
        self.assertEqual(terminal_ui.brand("PentAGI"), terminal_ui.BRANDS["pentagi"])
        with patch.dict(terminal_ui.os.environ, {"SOLVER_HARNESS": "strix"}):
            self.assertEqual(terminal_ui.brand(), terminal_ui.BRANDS["strix"], "the one its VM runs")
            self.assertEqual(terminal_ui.Look(24).accent, "38;2;255;212;59", "its signature colour")
            self.assertEqual(terminal_ui.Look(24).keys, "38;2;56;189;248", "the keys stay the screen's sky")
        outside = {name: value for name, value in terminal_ui.os.environ.items() if name != "SOLVER_HARNESS"}
        with patch.dict(terminal_ui.os.environ, outside, clear=True):
            self.assertEqual(terminal_ui.brand("Aider"), (terminal_ui.BLUE, terminal_ui.SKY))
            self.assertEqual(terminal_ui.Look(24).accent, LOOK.accent)

    def test_names_and_work_under_way_take_the_harness_colours_and_its_light(self):
        look = Look(24, terminal_ui.BRANDS["hexstrike"])
        first, signature = look.pair
        name = terminal_ui.gradient("HexStrike", look, terminal_ui.BOLD)
        self.assertEqual(plain(name), "HexStrike")
        self.assertEqual(name[0][1], "38;2;%d;%d;%d;1" % first)
        self.assertEqual(name[-1][1], "38;2;%d;%d;%d;1" % signature)
        lit = terminal_ui.gradient("HexStrike", look, light=4)
        self.assertIn(look.fg(terminal_ui.between(terminal_ui.between(first, signature, 0.5), terminal_ui.WHITE, 0.85)),
                      [how for _, how in lit], "white where the light is")
        band = terminal_ui.shimmer("http_repeater  url=http://127.0.0.1:8000", look, center=6)
        self.assertEqual(plain(band), "http_repeater  url=http://127.0.0.1:8000")
        self.assertEqual(band[-1][1], look.fg(terminal_ui.SOFT), "dimmer away from the light")
        self.assertIn(look.fg(terminal_ui.between(signature, terminal_ui.WHITE, 0.15)), [how for _, how in band])
        self.assertEqual(terminal_ui.shimmer("x", BARE), [("x", "")], "plain without colour")
        # The light crosses a line, then pauses off it.
        self.assertLess(terminal_ui.light_at(0.0, 20), 0)
        self.assertTrue(0 <= terminal_ui.light_at(0.5, 20) <= 20)

    def test_the_input_box_colours_flow_round_it_and_meet_at_the_corners(self):
        look = Look(24, terminal_ui.BRANDS["pentagi"])
        box = terminal_ui.Box(terminal_ui.Screen(), Editor(), "Tell PentAGI what to do", (("enter", "send"),), look)
        lines, _, _ = box.lines(40, 20, now=1.0)
        top, middle, bottom = lines[0], lines[1], lines[2]
        self.assertEqual(top[0][1], middle[0][1], "the top-left corner meets the side")
        self.assertEqual(bottom[0][1], middle[-1][1], "and so do the bottom and the other side")
        self.assertGreater(len({how for _, how in top}), 3, "a loop of its colours along the border")
        later, _, _ = box.lines(40, 20, now=2.0)
        self.assertNotEqual(later[0], top, "its colours move")
        self.assertEqual(plain(later[0]), plain(top))
        self.assertEqual(texts(box.lines(40, 20, now=1.0)[0])[:3], texts(terminal_ui.Box(
            terminal_ui.Screen(), Editor(), "Tell PentAGI what to do", (("enter", "send"),), BARE).lines(40, 20)[0])[:3])

    def test_the_banner_shows_with_a_light_passing_over_it_once(self):
        written, screen = [], terminal_ui.Screen()
        screen.write = written.append
        with patch.object(screen, "terminal", return_value=True), patch.object(screen, "width", return_value=100), \
                patch.object(screen, "height", return_value=30), patch.object(terminal_ui, "color_depth", return_value=24), \
                patch.object(terminal_ui.time, "sleep") as slept:
            screen.banner("Shannon", "a-model", [("Reach", "only the inference")])
        frames = [text for text in written if text.startswith("\x1b7")]
        self.assertGreater(len(frames), 10)
        self.assertTrue(all(text.count("\x1b7") == 3 and text.count("\x1b8") == 3 for text in frames),
                        "its three rows drawn again in place, the cursor kept")
        self.assertEqual(slept.call_count, len(frames) - 1, "the last one as it stays")
        self.assertLess(sum(call.args[0] for call in slept.call_args_list), 1.0, "under a second")
        self.assertIn("Shannon", terminal_ui.ESCAPES.sub("", frames[-1]))
        self.assertEqual(screen.harness, "Shannon", "the screen takes the banner's harness's colours")


class Drawing(unittest.TestCase):
    def test_markdown_draws_as_the_screen_draws_a_message(self):
        lines = markdown("# Findings\n\n**Bold** and `code` and *it*\n- one\n  - two\n1. first\n> quoted\n\n---\n"
                         "```http\nGET / HTTP/1.1\n```\n| a | bb |\n|---|---|\n| 1 | 2 |", 30, LOOK)
        self.assertEqual(texts(lines), [
            "Findings", "", "Bold and code and it", "• one", "  • two", "1. first", "│ quoted", "",
            "─" * 30, "  GET / HTTP/1.1", "a  bb", "─  ──", "1  2"])
        heading, styled = lines[0], dict(lines[2])
        self.assertEqual(heading[0][1], "38;2;56;189;248;1", "the first two heading levels in sky, bold")
        self.assertEqual(styled["Bold"], "1")
        self.assertEqual(styled["code"], "38;2;156;194;255")
        self.assertEqual(styled["it"], "3")

    def test_words_wrap_whole_and_hang_under_their_marker(self):
        lines = markdown("- a list item whose words go on past the edge of the screen", 24, LOOK)
        self.assertEqual(texts(lines), ["• a list item whose", "  words go on past the", "  edge of the screen"])
        self.assertEqual(texts(wrap([("abcdefghijkl", "")], 5)), ["abcde", "fghij", "kl"])
        self.assertTrue(all(len(line) <= 24 for line in texts(lines)))

    def test_text_from_a_tool_never_moves_the_cursor_or_colours_the_screen(self):
        cleaned = terminal_ui.clean("ok\x1b[2J\x1b[31mred\x1b[0m\x1b]0;title\x07\r\nnext\x00\tend")
        self.assertEqual(cleaned, "okred\nnext    end")
        self.assertEqual(texts(markdown("\x1b[1Ahi", 20, LOOK)), ["hi"])

    def test_without_colour_muted_text_is_dimmed_and_code_is_bold(self):
        self.assertEqual((BARE.muted, BARE.code, BARE.accent), ("2", "1", ""))
        self.assertEqual(terminal_ui.Look(8).accent, "38;5;%d" % terminal_ui.nearest_256(terminal_ui.SKY))
        self.assertEqual(terminal_ui.color_depth({"NO_COLOR": "1", "COLORTERM": "truecolor"}), 0)
        self.assertEqual(terminal_ui.color_depth({"COLORTERM": "truecolor"}), 24)
        self.assertEqual(terminal_ui.color_depth({}), 8)

    def test_plain_output_has_the_words_and_no_codes(self):
        screen = terminal_ui.Screen()
        with contextlib.redirect_stdout(io.StringIO()) as shown:
            screen.banner("HexStrike", "a-model", [("Tools", "16 tools"), ("Empty", "")])
            screen.user("Test only this target: http://127.0.0.1:8000.")
            screen.markdown("**Done.**")
            screen.row("Tool", "http_repeater  url=http://127.0.0.1:8000", "done", seconds=0.4)
            screen.output("HTTP/1.1 200 OK\n" + "line\n" * 6)
            screen.warn("Stopped after 24 tool rounds.")
            screen.error("The model could not answer: busy")
            with screen.step("Scan", "http://127.0.0.1:8000") as step:
                step.output = "found it"
        text = shown.getvalue()
        self.assertNotIn("\x1b", text)
        self.assertIn("    HexStrike", text)
        self.assertIn("Tools  16 tools", text)
        self.assertNotIn("Empty", text)
        self.assertIn("› Test only this target: http://127.0.0.1:8000.", text)
        self.assertIn("  Done.", text)
        self.assertRegex(text, r"◇ Tool +http_repeater  url=http://127.0.0.1:8000  done 0.4s")
        self.assertIn("      HTTP/1.1 200 OK", text)
        self.assertIn("… 3 more lines", text)
        self.assertIn("▲ Stopped after 24 tool rounds.", text)
        self.assertIn("■ The model could not answer: busy", text)
        self.assertRegex(text, r"◇ Scan +http://127.0.0.1:8000  done [0-9.]+s\n +found it")

    def test_a_failed_or_stopped_step_says_so(self):
        screen = terminal_ui.Screen()
        with contextlib.redirect_stdout(io.StringIO()) as shown:
            with self.assertRaises(ValueError), screen.step("Tool", "nmap_scan"):
                raise ValueError("nmap is not installed")
            with self.assertRaises(KeyboardInterrupt), screen.step("Scan", "http://x"):
                raise KeyboardInterrupt
            with screen.step("Check", "the model") as step:
                step.fail("refused")
        text = shown.getvalue()
        self.assertRegex(text, r"■ Tool +nmap_scan  failed [0-9.]+s\n +nmap is not installed")
        self.assertRegex(text, r"■ Scan +http://x  stopped")
        self.assertRegex(text, r"■ Check +the model  failed [0-9.]+s\n +refused")

    def test_without_a_terminal_the_line_is_read_with_input(self):
        with patch("builtins.input", return_value="scan http://x") as asked:
            self.assertEqual(terminal_ui.Screen().ask("placeholder", plain="shannon> "), "scan http://x")
        asked.assert_called_once_with("shannon> ")

    def test_inside_the_pane_the_box_says_whether_its_line_is_empty(self):
        # The Yougori screen's pane opens its own commands for a `/` typed at the start of an empty
        # line; one typed after text stays the harness's.
        written, screen = [], terminal_ui.Screen()
        screen.write, screen.width, screen.height = written.append, lambda: 40, lambda: 20
        box = terminal_ui.Box(screen, Editor(), "Tell HexStrike what to test next", (("enter", "send"),), LOOK)
        with patch.dict(terminal_ui.os.environ, {"YOUGORI_PANE": "1"}):
            box.draw()
            box.editor.insert("check /admin")
            box.draw()
            box.erase()
        self.assertTrue(written[0].endswith("\x1b]7717;line;empty\x07"), written[0])
        self.assertTrue(written[1].endswith("\x1b]7717;line;text\x07"), written[1])
        self.assertTrue(written[2].endswith("\x1b]7717;line;empty\x07"), written[2])
        outside = {name: value for name, value in terminal_ui.os.environ.items() if name != "YOUGORI_PANE"}
        with patch.dict(terminal_ui.os.environ, outside, clear=True):
            self.assertEqual(terminal_ui.line_word(True), "", "another terminal gets nothing of it")

    def test_a_hangup_ends_the_harness_as_ctrl_c_does_and_lets_its_cleanup_finish(self):
        # The pane stops a harness for /new or another model with a hangup: its servers must stop.
        import signal
        number = getattr(signal, "SIGHUP", signal.SIGTERM)
        before = signal.getsignal(number)
        if before != signal.SIG_DFL:
            self.skipTest("the test runner handles this signal itself")
        screen = terminal_ui.Screen()
        restore = screen.hang_up_cleanly()
        try:
            handler = signal.getsignal(number)
            with self.assertRaises(KeyboardInterrupt):
                handler(number, None)
            self.assertTrue(screen.hung_up.is_set(), "an input box on another thread ends too")
            self.assertIsNone(handler(number, None), "a second one lets the cleanup go on")
        finally:
            restore()
        self.assertEqual(signal.getsignal(number), before)
        # A harness that handles hangups itself (Shannon) keeps its own handler.
        own = lambda *_: None  # noqa: E731
        signal.signal(number, own)
        try:
            restore = terminal_ui.Screen().hang_up_cleanly()
            self.assertIs(signal.getsignal(number), own)
            restore()
            self.assertIs(signal.getsignal(number), own)
        finally:
            signal.signal(number, before)

    def test_the_brief_is_shown_whole_and_the_program_runs_in_its_place(self):
        with contextlib.redirect_stdout(io.StringIO()) as shown, patch.object(terminal_ui.os, "execvp") as run:
            terminal_ui.brief("PentestGPT asks for the task first. Paste this line as its answer:\n" + "word " * 60,
                              ["pentestgpt", "--log-dir", "reports/pentestgpt"])
        lines = shown.getvalue().split("\n")
        self.assertIn("  ◆ PentestGPT asks for the task first. Paste this line as its answer:", lines)
        self.assertIn("  " + ("word " * 60).strip(), lines, "one line however long, so it is copied whole")
        run.assert_called_once_with("pentestgpt", ["pentestgpt", "--log-dir", "reports/pentestgpt"])


class History(unittest.TestCase):
    """What a Yougori client tells the screen's pane of the chat (OSC 7718), and the earlier chat
    the screen gives it."""

    def test_the_chat_is_told_only_inside_the_pane(self):
        import base64
        import os
        with patch.dict(os.environ, {"YOUGORI_PANE": "1"}):
            word = terminal_ui.chat_word("turn", "Map the API ✓", "user")
            self.assertTrue(word.startswith("\x1b]7718;turn;user;") and word.endswith("\x07"), word)
            self.assertEqual(base64.b64decode(word[len("\x1b]7718;turn;user;"):-1]).decode(), "Map the API ✓")
            self.assertTrue(terminal_ui.chat_word("tool", "Web open").startswith("\x1b]7718;tool;"))
            long = terminal_ui.chat_word("turn", "é" * 100000, "assistant")
            self.assertLessEqual(len(long), 256 * 1024, "within what the pane reads")
            self.assertEqual(terminal_ui.chat_word("turn", "", "user"), "")
        with patch.dict(os.environ, {"YOUGORI_PANE": ""}):
            self.assertEqual(terminal_ui.chat_word("turn", "x", "user"), "")

    def test_the_earlier_chat_is_read_once(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "earlier-chat.md"
            path.write_text("[This conversation began earlier…]\nUser: hi\n", encoding="utf-8")
            with patch.dict(os.environ, {"YOUGORI_CONTEXT_FILE": str(path)}):
                self.assertEqual(terminal_ui.earlier_chat(), "[This conversation began earlier…]\nUser: hi")
                self.assertFalse(path.exists())
                self.assertEqual(terminal_ui.earlier_chat(), "")
        with patch.dict(os.environ, {"YOUGORI_CONTEXT_FILE": ""}):
            self.assertEqual(terminal_ui.earlier_chat(), "")


if __name__ == "__main__":
    unittest.main()
