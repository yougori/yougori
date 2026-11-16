"""PandasAI: conversational data analysis. PandasAI loads the CSV and Excel files in the person's
folder as its dataframes; each question becomes Python that PandasAI writes and runs on them, and
the answer comes back as words, a number, a table or a chart (charts are saved in the folder)."""

from terminal_ui import screen

NAME = "PandasAI"
ENGINE = "PandasAI 3 · questions become pandas code on your dataframes"
TAGLINE = "chat with your data"
PLACEHOLDER = "Ask a question about the data in your folder"
WORKING = "Analysing"
KINDS = ("*.csv", "*.xlsx", "*.xls", "*.parquet")


class Run:
    def __init__(self, kit):
        import pandasai as pai
        from pandasai_litellm.litellm import LiteLLM
        self.kit, self.pai = kit, pai
        llm = LiteLLM(model="openai/" + kit.model, api_base=kit.base_url, api_key=kit.api_key, max_tokens=kit.reply,
                      temperature=0)
        pai.config.set({"llm": llm, "save_logs": False, "verbose": False, "max_retries": 3})
        self.frames, names = [], []
        for path in sorted(p for kind in KINDS for p in kit.workspace.rglob(kind) if ".git" not in p.parts)[:20]:
            try:
                frame = pai.read_csv(str(path)) if path.suffix == ".csv" else pai.DataFrame(__import__("pandas").read_excel(path)
                                                                                           if path.suffix != ".parquet"
                                                                                           else __import__("pandas").read_parquet(path))
            except Exception as error:  # one unreadable file leaves the others
                screen.warn("%s could not be read: %s" % (path.name, error))
                continue
            self.frames.append(frame)
            names.append(path.name)
        self.tools_words = 'pandas code on your dataframes'
        self.facts = [("Data", ", ".join(names) if names else "none yet: put CSV or Excel files in the folder")]

    def turn(self, text):
        if not self.frames:
            return "There is no data to analyse yet: put CSV or Excel files in the folder and start again."
        with screen.step("Code", "PandasAI writes and runs pandas") as step:
            answer = self.frames[0].chat(text) if len(self.frames) == 1 else self.pai.chat(text, *self.frames)
            kind = type(answer).__name__
            step.note = kind.replace("Response", "").lower() or "answer"
            code = getattr(answer, "last_code_executed", None)
            if code:
                step.output = code
        value = getattr(answer, "value", answer)
        # PandasAI makes exports/charts in the folder whether or not it draws a chart.
        for empty in (self.kit.workspace / "exports" / "charts", self.kit.workspace / "exports"):
            try:
                empty.rmdir()
            except OSError:
                pass
        if kind.startswith("Chart") or (isinstance(value, str) and value.endswith(".png")):
            return "Chart saved: %s" % value
        if hasattr(value, "to_markdown"):
            try:
                return value.head(50).to_markdown(index=False)
            except Exception:
                return str(value.head(50))
        return str(value)


def start(kit):
    return Run(kit)
