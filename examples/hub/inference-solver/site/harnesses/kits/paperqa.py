"""PaperQA2: answers from scientific papers and documents, with citations. PaperQA reads the PDFs,
text, Markdown and HTML files in the person's folder into its Docs, gathers the passages that bear
on each question (each summarised for relevance), and answers citing them. Its embeddings are by
keywords (sparse): this inference serves no embedding model."""
import asyncio

from terminal_ui import screen

NAME = "PaperQA2"
ENGINE = "PaperQA2 · gather evidence from your papers, answer with citations"
TAGLINE = "answers from your papers, with citations"
PLACEHOLDER = "Ask a question about the papers in your folder"
WORKING = "Reading the papers"
KINDS = ("*.pdf", "*.txt", "*.md", "*.html")


class Run:
    def __init__(self, kit):
        from paperqa import Docs, Settings
        from paperqa.settings import AnswerSettings
        self.kit = kit
        name = "openai/" + kit.model
        router = {"model_list": [{"model_name": name, "litellm_params": {
            "model": name, "api_base": kit.base_url, "api_key": kit.api_key, "max_tokens": kit.reply, "temperature": 0.1}}]}
        self.settings = Settings(llm=name, llm_config=router, summary_llm=name, summary_llm_config=router, embedding="sparse",
                                 temperature=0.1, answer=AnswerSettings(evidence_k=8, answer_max_sources=5, max_concurrent_requests=2))
        self.docs = Docs()
        self.read = set()
        self.loop = asyncio.new_event_loop()
        files = self.files()
        self.tools_words = 'reading and citing the papers in your folder'
        self.facts = [("Papers", "%d files in your folder (PDF, text, Markdown, HTML)" % len(files))]
        if not files:
            self.hello = "Put PDFs or text files in the folder: PaperQA answers from them."

    def files(self):
        return sorted(p for kind in KINDS for p in self.kit.workspace.rglob(kind) if ".git" not in p.parts and ".yougori" not in p.parts)

    async def _turn(self, text):
        new = [p for p in self.files() if p not in self.read]
        if new:
            with screen.step("Read", "%d new file%s" % (len(new), "" if len(new) == 1 else "s")) as step:
                for path in new:
                    try:
                        await self.docs.aadd(path, citation=path.name, docname=path.stem, settings=self.settings)
                    except Exception as error:  # one unreadable file does not stop the others
                        step.output += "%s: %s\n" % (path.name, error)
                    self.read.add(path)
        if not self.read:
            return "There is nothing to read yet: put PDFs or text files in the folder and ask again."
        with screen.step("Evidence", "Gather and cite passages") as step:
            session = await self.docs.aquery(text, settings=self.settings)
            step.note = "%d passages" % len(getattr(session, "contexts", []) or [])
        return getattr(session, "formatted_answer", None) or getattr(session, "answer", "") or ""

    def turn(self, text):
        return self.loop.run_until_complete(self._turn(text))


def start(kit):
    return Run(kit)
