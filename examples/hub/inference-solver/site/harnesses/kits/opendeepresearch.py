"""Open Deep Research: LangChain's deep research graph. Its supervisor writes a research brief, hands
topics to researchers who work in parallel with the tools, compresses what they found and writes
the final report. The researchers' tools are this terminal's: web search and pages when the VM
reaches the web, and the person's files."""
from terminal_ui import screen

from kits import lc

NAME = "Open Deep Research"
ENGINE = "Open Deep Research · supervisor, parallel researchers, final report"
TAGLINE = "deep research reports"
PLACEHOLDER = "What should Open Deep Research look into?"
WORKING = "Researching"


class Run:
    def __init__(self, kit):
        import open_deep_research.deep_researcher as graph
        from langchain_core.tools import tool
        from open_deep_research.state import ResearchComplete
        self.kit = kit
        own = lc.tools(kit.pick("web_search", "fetch_url", "read_file", "list_files", "search_files"))

        async def tools(config):
            # The graph's researchers get this terminal's tools in place of a hosted search service.
            return [tool(ResearchComplete)] + own
        graph.get_all_tools = tools
        self.graph = graph.deep_researcher
        model = "openai:" + kit.model
        self.config = {"configurable": {
            "allow_clarification": False, "search_api": "none", "max_concurrent_research_units": 2,
            "max_researcher_iterations": 3, "max_react_tool_calls": 6,
            "summarization_model": model, "research_model": model, "compression_model": model, "final_report_model": model,
            "summarization_model_max_tokens": kit.reply, "research_model_max_tokens": kit.reply,
            "compression_model_max_tokens": kit.reply, "final_report_model_max_tokens": kit.reply,
            "max_content_length": 20000}, "recursion_limit": 200}
        self.facts = [("Sources", "web search and pages" if kit.web else "your folder's files (the VM does not reach the web)")]

    def turn(self, text):
        import asyncio
        return asyncio.run(self._turn(text))

    async def _turn(self, text):
        report = ""
        async for update in self.graph.astream({"messages": [{"role": "user", "content": text}]}, self.config, stream_mode="updates"):
            for node, value in (update or {}).items():
                if node == "write_research_brief" and isinstance(value, dict) and value.get("research_brief"):
                    screen.row("Plan", "Research brief written", "done")
                    screen.output(value["research_brief"], limit=3)
                elif node == "research_supervisor":
                    notes = (value or {}).get("notes") or []
                    screen.row("Research", "Researchers finished", "done", "%d notes" % len(notes) if notes else "")
                elif node == "final_report_generation" and isinstance(value, dict):
                    report = value.get("final_report") or report
        return report


def start(kit):
    return Run(kit)
