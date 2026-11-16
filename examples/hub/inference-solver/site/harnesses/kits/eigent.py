"""Eigent: a workforce of agents for desk work, on Eigent's own engine, CAMEL's Workforce. A
coordinator splits each task and hands the parts to Eigent's kinds of worker: a Developer (shell
and files), a Document worker (reads and writes the folder's documents) and, when the VM reaches
the web, a Search worker. Each part shows on a row as it ends."""
from terminal_ui import screen

NAME = "Eigent"
ENGINE = "Eigent workforce on CAMEL · coordinator, Developer, Document and Search workers"
TAGLINE = "a multi-agent workforce for desk work"
PLACEHOLDER = "Give the workforce a task"
WORKING = "The workforce is working"


class Run:
    def __init__(self, kit):
        from camel.agents import ChatAgent
        from camel.models import ModelFactory
        from camel.societies.workforce import Workforce
        from camel.toolkits import FunctionTool
        from camel.types import ModelPlatformType
        self.kit = kit

        def model():
            return ModelFactory.create(model_platform=ModelPlatformType.OPENAI_COMPATIBLE_MODEL, model_type=kit.model,
                                       url=kit.base_url, api_key=kit.api_key,
                                       model_config_dict={"max_tokens": kit.reply, "temperature": 0.2})

        def agent(role, tools=()):
            return ChatAgent(system_message=kit.instructions(role), model=model(), tools=[FunctionTool(t) for t in tools])

        self.workforce = Workforce("Eigent workforce", coordinator_agent=agent("You coordinate a workforce: you assign each part of a task to the worker best suited."),
                                   task_agent=agent("You split tasks into clear parts and put the workers' results together."),
                                   new_worker_agent=agent("You are a general worker.", kit.pick("read_file", "list_files", "write_file")),
                                   default_model=model(), graceful_shutdown_timeout=5, task_timeout_seconds=900)
        self.workforce.add_single_agent_worker(
            "Developer: writes and runs code and shell commands, and works with the project's files",
            worker=agent("You are Eigent's Developer agent.", kit.pick("run_command", "read_file", "write_file", "list_files", "search_files")))
        self.workforce.add_single_agent_worker(
            "Document worker: reads the folder's documents and writes reports, notes and other documents",
            worker=agent("You are Eigent's Document agent.", kit.pick("read_file", "write_file", "list_files", "search_files")))
        if kit.web:
            self.workforce.add_single_agent_worker(
                "Search worker: searches the web and reads pages",
                worker=agent("You are Eigent's Search agent.", kit.pick("web_search", "fetch_url")))
        self.count = 0
        self.facts = [("Workers", "Developer, Document" + (", Search" if kit.web else ""))]

    def turn(self, text):
        from camel.tasks import Task
        self.count += 1
        task = self.workforce.process_task(Task(content=text, id=str(self.count)))
        for sub in getattr(task, "subtasks", None) or []:
            screen.row("Part", " ".join(str(getattr(sub, "content", "")).split())[:90], "done")
        return getattr(task, "result", None) or ""


def start(kit):
    return Run(kit)
