"""smolagents: Hugging Face's code agent. The model writes Python that calls the tools, smolagents
runs it in its own interpreter and feeds back what it printed, step after step, keeping its memory
from turn to turn."""
from terminal_ui import screen

NAME = "smolagents"
ENGINE = "smolagents CodeAgent · actions written as Python"
TAGLINE = "agents that think in code"
PLACEHOLDER = "Tell the smolagents code agent what to do"
IMPORTS = ["json", "csv", "math", "statistics", "datetime", "re", "collections", "itertools", "pathlib", "os", "os.path",
           "glob", "subprocess", "shutil", "textwrap", "random", "time", "pandas", "numpy"]


def first_line(code):
    lines = [line for line in (code or "").strip().splitlines() if line.strip() and not line.strip().startswith("#")]
    return (lines[0] if lines else "code")[:90]


class Run:
    def __init__(self, kit):
        from smolagents import CodeAgent, OpenAIServerModel, tool
        self.kit = kit
        model = OpenAIServerModel(model_id=kit.model, api_base=kit.base_url, api_key=kit.api_key, max_tokens=kit.reply,
                                  temperature=0.2)
        import importlib.util
        # Only modules this VM has: smolagents refuses to start with one it cannot import.
        present = [name for name in IMPORTS if importlib.util.find_spec(name.split(".")[0]) is not None]
        self.agent = CodeAgent(tools=[tool(function) for function in kit.tools], model=model, max_steps=20,
                               additional_authorized_imports=present, verbosity_level=-1,
                               step_callbacks=[self.shown],
                               instructions=kit.instructions("You are a smolagents code agent: you act by writing Python."))
        self.facts = [("Code", "runs in smolagents' own Python interpreter in this VM")]

    def shown(self, step, agent=None):
        code = getattr(step, "code_action", None)
        if not code:
            return
        error = getattr(step, "error", None)
        screen.row("Python", first_line(code), "failed" if error else "done", "%d lines" % len(code.strip().splitlines()))
        seen = getattr(step, "observations", None) or (str(error) if error else "")
        if seen:
            screen.output(str(seen), limit=3)

    def turn(self, text):
        result = self.agent.run(text, reset=False)
        return str(result) if result is not None else ""


def start(kit):
    return Run(kit)
