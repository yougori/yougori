"""LiveKit Agents: a voice-agent session for customer service, run in text. LiveKit's own
AgentSession runs the agent (its turns, its function tools, its chat context) with LiveKit's OpenAI
plugin on the relay; the person types what a caller would say. The agent answers from the company
documents in the folder and can look things up in them with its tools."""
import asyncio


from kits.pipecat import documents

NAME = "LiveKit Agents"
ENGINE = "LiveKit Agents · AgentSession with function tools, in text"
TAGLINE = "voice agents for contact centres (here in text)"
PLACEHOLDER = "Speak as a caller would (typed)"
WORKING = "Answering"
QUIET_ENV = {"LIVEKIT_TELEMETRY": "0"}


class Run:
    def __init__(self, kit):
        self.kit = kit
        self.docs = documents(kit.workspace)
        self.loop = asyncio.new_event_loop()
        self.loop.run_until_complete(self._start())
        self.facts = [("Session", "AgentSession, text in and out"),
                      ("Knowledge", "%d documents from your folder" % len(self.docs) if self.docs else "none yet: put your FAQ or policies in the folder")]
        self.tools_words = "the company's documents (search and read)"

    async def _start(self):
        from livekit.agents import Agent, AgentSession, function_tool
        from livekit.plugins import openai
        kit = self.kit
        search = kit.pick("search_files")[0]
        read = kit.pick("read_file")[0]

        class Support(Agent):
            def __init__(self, docs):
                super().__init__(instructions=(
                    "You are the voice agent of the business whose documents follow, answering callers. Keep each answer "
                    "short and spoken-friendly. Answer only from the documents; when they do not cover the question, say so "
                    "and offer to pass the call to a person. Never invent prices, dates or policies.\n\n"
                    + ("\n\n".join(docs) or "(No documents yet.)")))

            @function_tool
            async def search_documents(self, words: str) -> str:
                """Search the company's documents for words and return the matching lines.

                Args:
                    words: What to look for, as a regular expression.
                """
                return search(words)

            @function_tool
            async def read_document(self, path: str) -> str:
                """Read one of the company's documents in full.

                Args:
                    path: The document's path in the folder.
                """
                return read(path)

        self.session = AgentSession(llm=openai.LLM(model=kit.model, base_url=kit.base_url, api_key=kit.api_key, temperature=0.3))
        await self.session.start(Support(self.docs))

    async def _turn(self, text):
        result = await self.session.run(user_input=text)
        said = []
        for event in getattr(result, "events", []) or []:
            item = getattr(event, "item", None)
            if getattr(item, "role", None) == "assistant":
                words = getattr(item, "text_content", None)
                if words:
                    said.append(words)
        return "\n\n".join(said)

    def turn(self, text):
        return self.loop.run_until_complete(self._turn(text))


def start(kit):
    return Run(kit)
