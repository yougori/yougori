"""FinRobot: AI4Finance's financial analysis agents on AutoGen. Its Market Analyst gathers a company's
data with FinRobot's own toolkits (Yahoo Finance prices; company profile, news and financials from
Finnhub when the person sets a Finnhub key) and writes the analysis. The data needs the web.
Research only, not financial advice."""
import os
from pathlib import Path

from terminal_ui import screen

NAME = "FinRobot"
ENGINE = "FinRobot on AutoGen · Market Analyst with financial toolkits"
TAGLINE = "financial analysis agents (research, not advice)"
PLACEHOLDER = "Ask for an analysis, such as: analyse NVDA's last month"
WORKING = "Analysing"


class Run:
    def __init__(self, kit):
        from finrobot.agents.workflow import SingleAssistant
        self.kit = kit
        work = Path.home() / ".yougori" / "finrobot"
        work.mkdir(parents=True, exist_ok=True)
        llm_config = {"config_list": [{"model": kit.model, "base_url": kit.base_url, "api_key": kit.api_key,
                                       "max_tokens": kit.reply}], "temperature": 0.2, "timeout": 600, "cache_seed": None}
        self.assistant = SingleAssistant("Market_Analyst", llm_config, max_consecutive_auto_reply=12, human_input_mode="NEVER",
                                         code_execution_config={"work_dir": str(work), "use_docker": False})
        keyed = bool(os.environ.get("FINNHUB_API_KEY"))
        self.tools_words = "FinRobot's financial data toolkits"
        self.facts = [("Agent", "Market Analyst"),
                      ("Data", "Yahoo Finance" + (" and Finnhub" if keyed else " (Finnhub tools need FINNHUB_API_KEY)")
                       + ("" if kit.web else "; the VM does not reach the web, so start Yougori with --internet full"))]
        self.hello = "Research only, not financial advice."

    def turn(self, text):
        import datetime
        import shutil
        proxy, analyst = self.assistant.user_proxy, self.assistant.assistant
        kept = {}
        reset = self.assistant.reset

        def keep():
            # FinRobot resets its agents as the chat ends: what they said is kept first.
            kept["messages"] = list(proxy.chat_messages.get(analyst, []))
            reset()
        self.assistant.reset = keep
        cache = self.kit.workspace / ".cache"
        had_cache = cache.exists()
        try:
            self.assistant.chat("Today is %s. %s\n(Reply TERMINATE when the analysis is complete.)"
                                % (datetime.date.today().isoformat(), text), use_cache=False)
        finally:
            self.assistant.reset = reset
            if not had_cache:
                shutil.rmtree(cache, ignore_errors=True)  # AutoGen's cache, not the person's
        said = []
        for message in kept.get("messages", []):
            for call in message.get("tool_calls") or []:
                function = call.get("function", {})
                screen.row("Tool", ("%s %s" % (function.get("name", "tool"), " ".join(str(function.get("arguments", "")).split())))[:100], "done")
            words = message.get("content")
            if message.get("name") == analyst.name and isinstance(words, str) and words.replace("TERMINATE", "").strip():
                said.append(words.replace("TERMINATE", "").strip())
        return said[-1] if said else ""


def start(kit):
    return Run(kit)
