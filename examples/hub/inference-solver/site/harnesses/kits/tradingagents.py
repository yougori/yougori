"""TradingAgents: a trading firm of agents analysing one company. Its market, news and fundamentals
analysts gather data (Yahoo Finance, so the VM needs the web), bull and bear researchers debate, a
trader proposes and the risk team and portfolio manager decide. Each run's reports are saved in
the folder's reports/. Research only, not financial advice."""
import datetime
import re
from pathlib import Path

from terminal_ui import screen

NAME = "TradingAgents"
ENGINE = "TradingAgents graph · analysts, researchers' debate, trader, risk team"
TAGLINE = "multi-agent market analysis (research, not advice)"
PLACEHOLDER = "A ticker and a date, such as NVDA 2026-11-13"
WORKING = "The firm is analysing"
TICKER = re.compile(r"\b([A-Z]{1,5}(?:[.-][A-Z]{1,4})?)\b")
DATE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")
REPORTS = (("market_report", "Market analyst"), ("sentiment_report", "Social analyst"), ("news_report", "News analyst"),
           ("fundamentals_report", "Fundamentals analyst"), ("investment_plan", "Research manager"),
           ("trader_investment_plan", "Trader"), ("final_trade_decision", "Portfolio manager"))


class Run:
    def __init__(self, kit):
        from tradingagents.default_config import DEFAULT_CONFIG
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        self.kit = kit
        home = Path.home() / ".tradingagents"
        config = dict(DEFAULT_CONFIG)
        config.update({"llm_provider": "openai", "backend_url": kit.base_url, "deep_think_llm": kit.model,
                       "quick_think_llm": kit.model, "max_tokens": kit.reply, "temperature": 0.2,
                       "data_cache_dir": str(home / "cache"), "memory_log_path": str(home / "memory" / "trading_memory.md"),
                       "results_dir": str(home / "results"), "max_debate_rounds": 1, "max_risk_discuss_rounds": 1})
        self.graph = TradingAgentsGraph(selected_analysts=("market", "news", "fundamentals"), config=config)
        self.tools_words = 'market, news and fundamentals data (Yahoo Finance)'
        self.facts = [("Data", "Yahoo Finance" + ("" if kit.web else ": the VM does not reach the web, so start Yougori with --internet full")),
                      ("Reports", "reports/tradingagents in your folder")]
        self.hello = "Research only, not financial advice. Give a ticker, and a date if not today."

    def turn(self, text):
        found = [t for t in TICKER.findall(text) if t not in ("I", "A", "AND", "OR", "THE", "USD")]
        if not found:
            return "Give a ticker, such as NVDA or BTC-USD, and a date if not today (2026-11-13)."
        ticker = found[0]
        day = (DATE.findall(text) or [datetime.date.today().isoformat()])[0]
        with screen.step("Firm", "%s on %s: analysts, debate, trader, risk" % (ticker, day)) as step:
            state, decision = self.graph.propagate(ticker, day)
            step.note = str(decision or "")[:40]
        folder = self.kit.workspace / "reports" / "tradingagents"
        folder.mkdir(parents=True, exist_ok=True)
        parts = []
        for key, who in REPORTS:
            words = (state or {}).get(key)
            if isinstance(words, str) and words.strip():
                screen.row("Report", who, "done", "%d words" % len(words.split()))
                parts.append("## %s\n\n%s" % (who, words.strip()))
        path = folder / ("%s-%s.md" % (ticker, day))
        path.write_text("# %s on %s\n\n%s\n" % (ticker, day, "\n\n".join(parts)), encoding="utf-8")
        final = (state or {}).get("final_trade_decision") or str(decision)
        return "%s\n\n*Decision: %s. All reports saved in %s. Research only, not financial advice.*" % (
            final, decision, path.relative_to(self.kit.workspace))


def start(kit):
    return Run(kit)
