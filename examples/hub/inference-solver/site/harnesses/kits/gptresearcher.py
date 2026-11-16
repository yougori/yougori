"""GPT Researcher: each question becomes a research report. GPT Researcher plans the research,
gathers sources (the web through DuckDuckGo when the VM reaches it, otherwise the documents in the
person's folder), and writes the report, which is saved in the folder's reports/ as Markdown."""
import asyncio
import os
import re
import time

from terminal_ui import screen

NAME = "GPT Researcher"
ENGINE = "GPT Researcher · plan, gather sources, write the report"
TAGLINE = "research reports with sources"
PLACEHOLDER = "What should GPT Researcher research?"
WORKING = "Researching"


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "report"


class Run:
    def __init__(self, kit):
        self.kit = kit
        model = "openai:" + kit.model
        budget = str(kit.reply)
        os.environ.update({"FAST_LLM": model, "SMART_LLM": model, "STRATEGIC_LLM": model,
                           "FAST_TOKEN_LIMIT": budget, "SMART_TOKEN_LIMIT": budget, "STRATEGIC_TOKEN_LIMIT": budget,
                           "SUMMARY_TOKEN_LIMIT": str(min(kit.reply, 1200)), "TEMPERATURE": "0.3",
                           "MAX_SUBTOPICS": "3", "MAX_ITERATIONS": "3", "TOTAL_WORDS": "1000",
                           "RETRIEVER": "duckduckgo", "DOC_PATH": str(kit.workspace), "SCRAPER": "bs",
                           "EMBEDDING": "openai:text-embedding-3-small"})
        # This inference serves no embedding model: GPT Researcher ranks its sources by shared words.
        from gpt_researcher.memory import embeddings as memory
        from kits import lexical
        memory.Memory.get_embeddings = lambda self: lexical.embeddings()
        self.source = "web" if kit.web else "local"
        self.tools_words = "web search and pages, or your folder's documents"
        self.facts = [("Sources", "the web, through DuckDuckGo" if kit.web else "the documents in your folder (the VM does not reach the web)"),
                      ("Reports", "reports/ in your folder")]

    async def _research(self, query):
        from gpt_researcher import GPTResearcher
        researcher = GPTResearcher(query=query, report_type="research_report", report_source=self.source, verbose=False)
        with screen.step("Plan", "Research: " + query[:90]) as step:
            await researcher.conduct_research()
            urls = researcher.get_source_urls() if hasattr(researcher, "get_source_urls") else []
            step.note = "%d sources" % len(urls) if urls else ""
            if urls:
                step.output = "\n".join(urls[:12])
        with screen.step("Write", "The report") as step:
            report = await researcher.write_report()
            step.note = "%d words" % len(report.split())
        return report

    def turn(self, text):
        report = asyncio.run(self._research(text))
        folder = self.kit.workspace / "reports"
        folder.mkdir(exist_ok=True)
        path = folder / ("%s-%s.md" % (time.strftime("%Y%m%d-%H%M"), slug(text)))
        path.write_text(report, encoding="utf-8")
        return report + "\n\n*Saved in %s.*" % path.relative_to(self.kit.workspace)


def start(kit):
    return Run(kit)
