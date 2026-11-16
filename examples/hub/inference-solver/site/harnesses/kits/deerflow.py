"""DeerFlow: ByteDance's deep research workflow (its 1.x graph). A coordinator takes the question, a
planner writes a research plan, researchers search the web (DuckDuckGo, when the VM reaches it) and
a coder runs Python, then a reporter writes the report, saved in the folder's reports/. Each agent's
step shows on a row."""
import asyncio
import os
import re
import time
from pathlib import Path

from terminal_ui import screen

NAME = "DeerFlow"
ENGINE = "DeerFlow 1.x graph · coordinator, planner, researchers, coder, reporter"
TAGLINE = "deep research with a team of agents"
PLACEHOLDER = "What should DeerFlow research?"
WORKING = "Researching"


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "report"


class Run:
    def __init__(self, kit):
        self.kit = kit
        home = Path.home() / ".yougori" / "deerflow"
        home.mkdir(parents=True, exist_ok=True)
        conf = home / "conf.yaml"
        quote = lambda value: '"%s"' % str(value).replace("\\", "\\\\").replace('"', '\\"')
        conf.write_text("BASIC_MODEL:\n  base_url: %s\n  model: %s\n  api_key: %s\n  max_retries: 2\n  token_limit: %d\n"
                        % (quote(kit.base_url), quote(kit.model), quote(kit.api_key), max(kit.window - kit.reply - 2000, 4096)),
                        encoding="utf-8")
        os.environ["SEARCH_API"] = "duckduckgo"
        # DeerFlow reads conf.yaml beside its source; this session's is in its home.
        import src.llms.llm as llms
        llms._get_config_file_path = lambda: str(conf)
        from src.workflow import graph
        self.graph = graph
        self.tools_words = 'web search and Python, its own'
        self.facts = [("Agents", "coordinator, planner, researcher, coder, reporter"),
                      ("Search", "DuckDuckGo" if kit.web else "none: the VM does not reach the web"),
                      ("Reports", "reports/ in your folder")]

    async def _turn(self, text):
        state = {"messages": [{"role": "user", "content": text}], "auto_accepted_plan": True,
                 "enable_background_investigation": self.kit.web, "research_topic": text, "clarified_research_topic": text}
        config = {"configurable": {"thread_id": str(time.time()), "max_plan_iterations": 1, "max_step_num": 3,
                                   "mcp_settings": {"servers": {}}}, "recursion_limit": 100}
        seen, final = 0, {}
        async for value in self.graph.astream(input=state, config=config, stream_mode="values"):
            final = value if isinstance(value, dict) else final
            messages = final.get("messages", []) if isinstance(final, dict) else []
            for message in messages[seen:]:
                who = getattr(message, "name", None) or (message.get("name") if isinstance(message, dict) else None)
                words = getattr(message, "content", None) if not isinstance(message, dict) else message.get("content")
                if who:
                    screen.row(str(who).capitalize()[:7], " ".join(str(words or "").split())[:100], "done")
            seen = len(messages)
        return final.get("final_report") or ""

    def turn(self, text):
        report = asyncio.run(self._turn(text))
        if report:
            folder = self.kit.workspace / "reports"
            folder.mkdir(exist_ok=True)
            path = folder / ("%s-%s.md" % (time.strftime("%Y%m%d-%H%M"), slug(text)))
            path.write_text(report, encoding="utf-8")
            report += "\n\n*Saved in %s.*" % path.relative_to(self.kit.workspace)
        return report


def start(kit):
    return Run(kit)
