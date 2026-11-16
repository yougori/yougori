"""TaskWeaver: Microsoft's code-first agent for data analysis. Its Planner breaks each request into
steps and its Code Interpreter writes and runs Python for them in a local Jupyter kernel in this VM,
keeping the variables from step to step, so data loaded once stays loaded. It works in the folder."""
import json
import shutil
from pathlib import Path

from terminal_ui import screen

NAME = "TaskWeaver"
ENGINE = "TaskWeaver · Planner and Code Interpreter on a local kernel"
TAGLINE = "code-first data analysis"
PLACEHOLDER = "Ask TaskWeaver to analyse something in your folder"
WORKING = "TaskWeaver is working"
SOURCE = Path("/opt/solver/source")


def project(kit):
    """TaskWeaver's project folder in its home: its own template, with this session's model."""
    target = Path.home() / ".yougori" / "taskweaver"
    if target.exists():
        shutil.rmtree(target)
    template = next((p for p in (SOURCE / "project", Path(__import__("taskweaver").__file__).resolve().parents[1] / "project")
                     if p.is_dir()), None)
    if template:
        shutil.copytree(template, target, ignore=shutil.ignore_patterns("sample_data"))
    else:
        (target / "plugins").mkdir(parents=True)
    (target / "taskweaver_config.json").write_text(json.dumps({
        "llm.api_base": kit.base_url, "llm.api_key": kit.api_key, "llm.api_type": "openai", "llm.model": kit.model,
        # TaskWeaver asks top_p 0 and 1,024 tokens by default; the model servers refuse a top_p of 0.
        "llm.openai.top_p": 0.95, "llm.openai.max_tokens": kit.reply, "llm.openai.temperature": 0.0,
        "execution_service.kernel_mode": "local", "session.max_internal_chat_round_num": 12,
        "logging.log_folder": str(target / "logs")}, indent=2), encoding="utf-8")
    return target


class Run:
    def __init__(self, kit):
        from taskweaver.app.app import TaskWeaverApp
        self.kit = kit
        self.app = TaskWeaverApp(app_dir=str(project(kit)))
        self.session = self.app.get_session()
        self.tools_words = 'Python in a local kernel'
        self.facts = [("Roles", "Planner and Code Interpreter"), ("Code", "Python in a local Jupyter kernel in this VM")]

    def turn(self, text):
        text = "%s\n(The person's files are in %s; work there.)" % (text, self.kit.workspace)
        chat = self.session.send_message(text)
        answer = ""
        for post in getattr(chat, "post_list", []) or []:
            sender, receiver = getattr(post, "send_from", ""), getattr(post, "send_to", "")
            message = " ".join(str(getattr(post, "message", "") or "").split())
            if receiver == "User":
                answer = str(getattr(post, "message", "") or "")
                continue
            code = next((a.content for a in getattr(post, "attachment_list", []) or []
                         if str(getattr(a, "type", "")).lower().endswith("reply_content") and getattr(a, "content", "")), "")
            screen.row(sender[:7] or "Step", (message or code or "…")[:100], "done", "→ " + receiver if receiver else "")
        return answer


def start(kit):
    return Run(kit)
