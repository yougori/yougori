"""Pipecat: a customer-service conversation on Pipecat's own pipeline (its context aggregators and
its OpenAI LLM service, the frames running between them), in text: the person types as a customer
would talk. The agent follows the company documents in the folder (Markdown and text files), and
says when something needs a person."""
import asyncio

NAME = "Pipecat"
ENGINE = "Pipecat pipeline · context aggregators and LLM service, in text"
TAGLINE = "conversation pipelines for support and voice"
PLACEHOLDER = "Write as a customer would"
WORKING = "Answering"
DOCUMENTS = ("*.md", "*.txt")
MOST = 24000


def documents(folder):
    """The company's documents: the folder's Markdown and text files, at most MOST characters."""
    found, used = [], 0
    for kind in DOCUMENTS:
        for path in sorted(folder.rglob(kind)):
            if ".git" in path.parts or ".yougori" in path.parts or used >= MOST:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")[:MOST - used]
            found.append("## %s\n\n%s" % (path.relative_to(folder), text))
            used += len(text)
    return found


class Run:
    def __init__(self, kit):
        self.kit = kit
        self.docs = documents(kit.workspace)
        self.loop = asyncio.new_event_loop()
        self.loop.run_until_complete(self._start())
        self.facts = [("Pipeline", "user aggregator → LLM → assistant aggregator"),
                      ("Knowledge", "%d documents from your folder" % len(self.docs) if self.docs else "none yet: put your FAQ or policies in the folder")]
        self.tools_words = "the company's documents"

    async def _start(self):
        from pipecat.frames.frames import LLMFullResponseEndFrame, LLMTextFrame
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.runner import PipelineRunner
        from pipecat.pipeline.task import PipelineTask
        from pipecat.processors.aggregators.llm_context import LLMContext
        from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
        from pipecat.processors.frame_processor import FrameProcessor
        from pipecat.services.openai.llm import OpenAILLMService

        system = ("You are the customer service agent of the business whose documents follow. Answer the customer briefly, "
                  "warmly and only from the documents. When they do not cover the question, say so and offer to pass it to a "
                  "person. Never invent prices, dates or policies.\n\n" + ("\n\n".join(self.docs) or "(No documents yet.)"))
        try:
            llm = OpenAILLMService(model=self.kit.model, api_key=self.kit.api_key, base_url=self.kit.base_url,
                                   params=OpenAILLMService.InputParams(max_tokens=self.kit.reply, temperature=0.3))
        except (AttributeError, TypeError):
            llm = OpenAILLMService(model=self.kit.model, api_key=self.kit.api_key, base_url=self.kit.base_url)
        pair = LLMContextAggregatorPair(LLMContext(messages=[{"role": "system", "content": system}]))
        self.words, self.done = [], asyncio.Event()
        run = self

        class Collect(FrameProcessor):
            async def process_frame(self, frame, direction):
                await super().process_frame(frame, direction)
                if isinstance(frame, LLMTextFrame):
                    run.words.append(frame.text)
                elif isinstance(frame, LLMFullResponseEndFrame):
                    run.done.set()
                await self.push_frame(frame, direction)

        self.task = PipelineTask(Pipeline([pair.user(), llm, Collect(), pair.assistant()]))
        self.runner = PipelineRunner(handle_sigint=False)
        self.running = asyncio.ensure_future(self.runner.run(self.task))
        await asyncio.sleep(0.2)

    async def _turn(self, text):
        from pipecat.frames.frames import LLMMessagesAppendFrame
        self.words.clear()
        self.done.clear()
        await self.task.queue_frames([LLMMessagesAppendFrame([{"role": "user", "content": text}], run_llm=True)])
        await asyncio.wait_for(self.done.wait(), 600)
        return "".join(self.words)

    def turn(self, text):
        return self.loop.run_until_complete(self._turn(text))


def start(kit):
    return Run(kit)
