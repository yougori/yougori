"""Biomni: Stanford's biomedical research agent (A1). It plans, writes and runs code for each step
(Python in this VM) and reasons over the results, in commercial mode: only datasets and tools whose
licences allow commercial use. The large data lake is not downloaded; the person's own data in the
folder is what it works on."""
from pathlib import Path

from terminal_ui import screen

NAME = "Biomni"
ENGINE = "Biomni A1 · plans, writes and runs code for biomedical tasks"
TAGLINE = "a biomedical research agent"
PLACEHOLDER = "Give Biomni a biomedical task"
WORKING = "Biomni is working"


class Run:
    def __init__(self, kit):
        from biomni.agent import A1
        self.kit = kit
        data = Path.home() / ".yougori" / "biomni"
        data.mkdir(parents=True, exist_ok=True)
        self.agent = A1(path=str(data), llm=kit.model, source="Custom", base_url=kit.base_url, api_key=kit.api_key,
                        commercial_mode=True, expected_data_lake_files=[], timeout_seconds=600)
        self.tools_words = "Python it writes and runs, Biomni's biomedical tools"
        self.facts = [("Mode", "commercial: only commercially licensed datasets and tools"),
                      ("Data", "your folder (the data lake is not downloaded)")]

    def turn(self, text):
        task = "%s\n\n(The person's files are in %s.)" % (text, self.kit.workspace)
        with screen.step("Agent", "Plan, run code, reason") as step:
            log, answer = self.agent.go(task)
            steps = sum(1 for entry in log or [] if "<execute>" in str(entry))
            step.note = "%d code runs" % steps if steps else ""
        if isinstance(answer, str) and "<solution>" in answer:
            answer = answer.split("<solution>", 1)[1].split("</solution>", 1)[0]
        return str(answer or "")


def start(kit):
    return Run(kit)
